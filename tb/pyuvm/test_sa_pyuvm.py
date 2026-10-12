"""
test_sa_pyuvm.py - UVM-style testbench for sa_top, written with pyuvm 3.x.

Structure (mirrors a SystemVerilog UVM bench):

  SaTest
   └── SaEnv
        ├── SaAgent
        │    ├── uvm_sequencer      (MatmulItem)
        │    ├── SaDriver           drives s_axis, publishes the stimulus (ap)
        │    └── SaMonitor          samples m_axis, publishes C tiles   (ap)
        ├── SaScoreboard            compares DUT C against NumPy golden
        └── SaCoverage              bins on K, operand sign/extremes, input bubbles,
                                    output back-pressure, stall patterns

Run:  make              (Icarus, N=4)
      make N=8
"""
from __future__ import annotations

import json
import os
import random

import cocotb
import numpy as np
import pyuvm
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge
from pyuvm import (ConfigDB, uvm_agent, uvm_analysis_port, uvm_component,
                   uvm_driver, uvm_env, uvm_sequence, uvm_sequence_item,
                   uvm_sequencer, uvm_test, uvm_tlm_analysis_fifo)

from sa_utils import (golden, pack_beat, params, start_clock_and_reset,
                      unpack_row)

N = params()


# --------------------------------------------------------------------- item
class MatmulItem(uvm_sequence_item):
    """One GEMM tile job: A (N x K) and B (K x N)."""

    def __init__(self, name="MatmulItem", k_min=1, k_max=64, mode="random",
                 a_sign=0, b_sign=0, pattern="none", gap_len=0):
        super().__init__(name)
        self.k_min, self.k_max, self.mode = k_min, k_max, mode
        self.a_sign, self.b_sign = a_sign, b_sign   # +1: all >= 0, -1: all <= 0, 0: free
        self.pattern, self.gap_len = pattern, gap_len   # input stall pattern (SaDriver.drive)
        self.bubbles = 0            # idle input cycles inserted by the driver (for coverage)
        self.A = None
        self.B = None

    def randomize(self):
        K = random.randint(self.k_min, self.k_max)
        if self.mode == "extreme":
            vals = [-128, 127]
            self.A = np.array([[random.choice(vals) for _ in range(K)] for _ in range(N)])
            self.B = np.array([[random.choice(vals) for _ in range(N)] for _ in range(K)])
        elif self.mode == "sparse":
            self.A = np.random.randint(-128, 128, size=(N, K))
            self.B = np.random.randint(-128, 128, size=(K, N))
            self.A[np.random.rand(N, K) < 0.7] = 0
        else:
            self.A = np.random.randint(-128, 128, size=(N, K))
            self.B = np.random.randint(-128, 128, size=(K, N))
        if self.a_sign:     # sign-constrained operands (all-positive / all-negative products)
            self.A = np.maximum(self.A, 0) if self.a_sign > 0 else np.minimum(self.A, 0)
        if self.b_sign:
            self.B = np.maximum(self.B, 0) if self.b_sign > 0 else np.minimum(self.B, 0)

    def __str__(self):
        return f"{self.get_name()} K={self.A.shape[1]} mode={self.mode}"


# ---------------------------------------------------------------- sequences
class RandomSeq(uvm_sequence):
    async def body(self):
        for _ in range(30):
            item = MatmulItem("rand")
            await self.start_item(item)
            item.randomize()
            await self.finish_item(item)


class CornerSeq(uvm_sequence):
    async def body(self):
        for mode, kmin, kmax in [("extreme", 64, 64), ("extreme", 1, 1),
                                 ("sparse", 1, 64), ("random", 1, 1)]:
            for _ in range(5):
                item = MatmulItem(mode, k_min=kmin, k_max=kmax, mode=mode)
                await self.start_item(item)
                item.randomize()
                await self.finish_item(item)


class KBoundarySeq(uvm_sequence):
    """K just below, equal to and just above the array size N."""
    async def body(self):
        for K in (max(N - 1, 1), N, N + 1):
            item = MatmulItem("kbound", k_min=K, k_max=K)
            await self.start_item(item)
            item.randomize()
            await self.finish_item(item)


class SignSeq(uvm_sequence):
    """All four sign combinations of (A, B): every product has the same / mixed sign."""
    async def body(self):
        for a_sign, b_sign in [(1, 1), (-1, -1), (1, -1), (-1, 1)]:
            item = MatmulItem("sign", k_min=1, k_max=32, a_sign=a_sign, b_sign=b_sign)
            await self.start_item(item)
            item.randomize()
            await self.finish_item(item)


class StallSeq(uvm_sequence):
    """Input stall patterns: valid toggling every cycle, and one long gap mid-job."""
    async def body(self):
        for pattern, gap_len in [("toggle", 0), ("gap", 25), ("toggle", 0), ("gap", 60)]:
            item = MatmulItem(pattern, k_min=4, k_max=40, pattern=pattern, gap_len=gap_len)
            await self.start_item(item)
            item.randomize()
            await self.finish_item(item)


# ------------------------------------------------------------------- driver
class SaDriver(uvm_driver):
    def build_phase(self):
        self.ap = uvm_analysis_port("ap", self)
        self.p_valid = ConfigDB().get(self, "", "p_valid")

    def start_of_simulation_phase(self):
        self.dut = cocotb.top

    async def run_phase(self):
        while True:
            item = await self.seq_item_port.get_next_item()
            self.ap.write(item)                  # expected-side of scoreboard
            await self.drive(item)
            self.seq_item_port.item_done()

    async def bubble(self, item):
        """One idle input cycle (counted per job for the stall coverage bins)."""
        self.dut.s_axis_tvalid.value = 0
        item.bubbles += 1
        await RisingEdge(self.dut.clk)

    async def drive(self, item):
        dut = self.dut
        K = item.A.shape[1]
        item.bubbles = 0
        for k in range(K):
            if item.pattern == "toggle":            # valid = 1,0,1,0,... every cycle
                await self.bubble(item)
            elif item.pattern == "gap" and k == K // 2:     # one long gap mid-job
                for _ in range(item.gap_len):
                    await self.bubble(item)
            while random.random() >= self.p_valid:  # random bubbles (stress test)
                await self.bubble(item)
            dut.s_axis_tvalid.value = 1
            dut.s_axis_tdata.value = pack_beat(item.A[:, k], item.B[k, :], N)
            dut.s_axis_tlast.value = int(k == K - 1)
            while True:
                await ReadOnly()
                accepted = int(dut.s_axis_tready.value) == 1
                await RisingEdge(dut.clk)
                if accepted:
                    break
        dut.s_axis_tvalid.value = 0
        dut.s_axis_tlast.value = 0


# ------------------------------------------------------------------ monitor
class SaMonitor(uvm_component):
    """Samples m_axis handshakes and drives tready: random, plus optional mid-output holds.

    `ready_holds` (tuple of cycle counts): in job j, after half of the N rows have been
    received, tready is held low for ready_holds[j % len] cycles. `bp_ap` publishes, per
    job, how many cycles tvalid was high while tready was low (back-pressure coverage).
    """

    def build_phase(self):
        self.ap = uvm_analysis_port("ap", self)
        self.bp_ap = uvm_analysis_port("bp_ap", self)
        self.p_ready = ConfigDB().get(self, "", "p_ready")
        self.ready_holds = ConfigDB().get(self, "", "ready_holds")

    def start_of_simulation_phase(self):
        self.dut = cocotb.top

    async def run_phase(self):
        dut = self.dut
        rows = []
        job, bp, hold_left, held = 0, 0, 0, False
        while True:
            if self.ready_holds and not held and len(rows) == max(N // 2, 1):
                held = True
                hold_left = self.ready_holds[job % len(self.ready_holds)]
            if hold_left > 0:
                ready = 0
                hold_left -= 1
            else:
                ready = int(random.random() < self.p_ready)
            dut.m_axis_tready.value = ready
            await ReadOnly()
            valid = int(dut.m_axis_tvalid.value) == 1
            if valid and not ready:
                bp += 1
            if ready and valid:
                rows.append(unpack_row(int(dut.m_axis_tdata.value), N))
                if int(dut.m_axis_tlast.value) == 1:
                    if len(rows) != N:
                        self.logger.error(f"tlast after {len(rows)} rows, expected {N}")
                    self.ap.write(np.array(rows, dtype=np.int64))
                    self.bp_ap.write(bp)
                    rows, bp, held, hold_left = [], 0, False, 0
                    job += 1
            await RisingEdge(dut.clk)


class SaAgent(uvm_agent):
    def build_phase(self):
        self.seqr = uvm_sequencer("seqr", self)
        self.driver = SaDriver("driver", self)
        self.monitor = SaMonitor("monitor", self)

    def connect_phase(self):
        self.driver.seq_item_port.connect(self.seqr.seq_item_export)


# --------------------------------------------------------------- scoreboard
class SaScoreboard(uvm_component):
    def build_phase(self):
        self.exp_fifo = uvm_tlm_analysis_fifo("exp_fifo", self)
        self.act_fifo = uvm_tlm_analysis_fifo("act_fifo", self)
        self.exp_export = self.exp_fifo.analysis_export
        self.act_export = self.act_fifo.analysis_export
        self.passed = 0
        self.failed = 0

    def check_phase(self):
        while True:
            got_e, item = self.exp_fifo.try_get()
            if not got_e:
                break
            got_a, C = self.act_fifo.try_get()
            if not got_a:
                self.logger.error(f"No DUT result for {item}")
                self.failed += 1
                continue
            exp = golden(item.A, item.B)
            if np.array_equal(C, exp):
                self.passed += 1
            else:
                self.failed += 1
                self.logger.error(f"MISMATCH for {item}\nexp=\n{exp}\ngot=\n{C}")
        extra, _ = self.act_fifo.try_get()
        if extra:
            self.logger.error("DUT produced more results than jobs sent")
            self.failed += 1

    def report_phase(self):
        self.logger.info(f"Scoreboard: {self.passed} passed, {self.failed} failed")
        assert self.failed == 0 and self.passed > 0, "scoreboard reported failures"


# ----------------------------------------------------------------- coverage
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "results")

# count buckets shared by the input-stall and back-pressure bins (cycles per job)
COUNT_BUCKETS = (("0", 0, 0), ("1-4", 1, 4), ("5-16", 5, 16),
                 ("17-49", 17, 49), ("50+", 50, 10**9))


def count_bin(prefix: str, v: int) -> str:
    return next(f"{prefix} {name}" for name, lo, hi in COUNT_BUCKETS if lo <= v <= hi)


def sign_class(M: np.ndarray) -> str:
    return "pos" if (M >= 0).all() else "neg" if (M <= 0).all() else "mix"


class SaCoverage(uvm_component):
    """Functional coverage, sampled once per job.

    Inputs: the stimulus item (driver.ap, carries K, operands, #input bubbles) and the
    per-job back-pressure cycle count (monitor.bp_ap). Jobs are in order, so the two
    streams are paired by position. Each test writes results/cov_<test>_N<N>.json;
    analysis/merge_coverage.py merges them and checks that every bin is hit.
    """

    BIN_NAMES = (
        ["K=1", "K 2-8", "K 9-32", "K 33-64", "K<N", "K=N", "K>N",
         "has -128", "has 127"]
        + [f"{op}:{s}" for op in "AB" for s in ("pos", "neg", "mix")]
        + [f"bubbles {name}" for name, _, _ in COUNT_BUCKETS]
        + [f"bp {name}" for name, _, _ in COUNT_BUCKETS]
        + ["pattern toggle", "pattern gap"]
    )

    def build_phase(self):
        self.item_fifo = uvm_tlm_analysis_fifo("item_fifo", self)
        self.bp_fifo = uvm_tlm_analysis_fifo("bp_fifo", self)
        self.item_export = self.item_fifo.analysis_export
        self.bp_export = self.bp_fifo.analysis_export
        self.test_name = ConfigDB().get(self, "", "test_name")
        self.hits = {b: 0 for b in self.BIN_NAMES}

    def hit(self, name: str):
        self.hits[name] += 1

    def sample(self, item, bp):
        K = item.A.shape[1]
        for name, lo, hi in (("K=1", 1, 1), ("K 2-8", 2, 8), ("K 9-32", 9, 32),
                             ("K 33-64", 33, 64)):
            if lo <= K <= hi:
                self.hit(name)
        self.hit("K<N" if K < N else "K=N" if K == N else "K>N")
        if (item.A == -128).any() or (item.B == -128).any():
            self.hit("has -128")
        if (item.A == 127).any() or (item.B == 127).any():
            self.hit("has 127")
        self.hit(f"A:{sign_class(item.A)}")
        self.hit(f"B:{sign_class(item.B)}")
        self.hit(count_bin("bubbles", item.bubbles))
        if bp is not None:
            self.hit(count_bin("bp", bp))
        if item.pattern in ("toggle", "gap"):
            self.hit(f"pattern {item.pattern}")

    def check_phase(self):
        while True:
            got_item, item = self.item_fifo.try_get()
            if not got_item:
                break
            got_bp, bp = self.bp_fifo.try_get()
            self.sample(item, bp if got_bp else None)

    def report_phase(self):
        covered = sum(1 for v in self.hits.values() if v > 0)
        self.logger.info(f"Coverage ({self.test_name}, N={N}): "
                         f"{covered}/{len(self.hits)} bins hit -> {self.hits}")
        os.makedirs(RESULTS_DIR, exist_ok=True)
        path = os.path.join(RESULTS_DIR, f"cov_{self.test_name}_N{N}.json")
        with open(path, "w") as f:
            json.dump({"test": self.test_name, "N": N, "hits": self.hits}, f, indent=1)


# ---------------------------------------------------------------------- env
class SaEnv(uvm_env):
    def build_phase(self):
        self.agent = SaAgent("agent", self)
        self.scoreboard = SaScoreboard("scoreboard", self)
        self.coverage = SaCoverage("coverage", self)

    def connect_phase(self):
        self.agent.driver.ap.connect(self.scoreboard.exp_export)
        self.agent.driver.ap.connect(self.coverage.item_export)
        self.agent.monitor.ap.connect(self.scoreboard.act_export)
        self.agent.monitor.bp_ap.connect(self.coverage.bp_export)


# --------------------------------------------------------------------- tests
class SaBaseTest(uvm_test):
    P_VALID = 1.0
    P_READY = 1.0
    READY_HOLDS = ()            # cycles to hold tready low mid-output, cycled per job
    SEQS = (RandomSeq, CornerSeq, KBoundarySeq, SignSeq)

    def build_phase(self):
        random.seed(1234)
        np.random.seed(1234)
        ConfigDB().set(None, "*", "p_valid", self.P_VALID)
        ConfigDB().set(None, "*", "p_ready", self.P_READY)
        ConfigDB().set(None, "*", "ready_holds", self.READY_HOLDS)
        ConfigDB().set(None, "*", "test_name", type(self).__name__)
        self.env = SaEnv("env", self)

    async def run_phase(self):
        self.raise_objection()
        await start_clock_and_reset(cocotb.top)
        for seq_cls in self.SEQS:
            await seq_cls("seq").start(self.env.agent.seqr)
        # let the last result drain out of the DUT
        await ClockCycles(cocotb.top.clk, 20 * N + 200)
        self.drop_objection()


@pyuvm.test()
class SaRandomTest(SaBaseTest):
    """Random + corner jobs, no stalls."""


@pyuvm.test()
class SaStressTest(SaBaseTest):
    """Same sequences with random input bubbles and output back-pressure."""
    P_VALID = 0.6
    P_READY = 0.4


@pyuvm.test()
class SaStallPatternTest(SaBaseTest):
    """Directed stall patterns: valid toggling every cycle, long input gaps, and tready
    held low for 8 / 30 / 50 cycles in the middle of the output stream."""
    READY_HOLDS = (8, 30, 50)
    SEQS = (StallSeq,)
