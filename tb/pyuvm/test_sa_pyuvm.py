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
        └── SaCoverage              bins on K and operand sign/extremes

Run:  make              (Icarus, N=4)
      make N=8
"""
from __future__ import annotations

import random

import cocotb
import numpy as np
import pyuvm
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge
from pyuvm import (ConfigDB, uvm_agent, uvm_analysis_port, uvm_component,
                   uvm_driver, uvm_env, uvm_sequence, uvm_sequence_item,
                   uvm_sequencer, uvm_subscriber, uvm_test,
                   uvm_tlm_analysis_fifo)

from sa_utils import (golden, pack_beat, params, start_clock_and_reset,
                      unpack_row)

N = params()


# --------------------------------------------------------------------- item
class MatmulItem(uvm_sequence_item):
    """One GEMM tile job: A (N x K) and B (K x N)."""

    def __init__(self, name="MatmulItem", k_min=1, k_max=64, mode="random"):
        super().__init__(name)
        self.k_min, self.k_max, self.mode = k_min, k_max, mode
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

    async def drive(self, item):
        dut = self.dut
        K = item.A.shape[1]
        for k in range(K):
            while random.random() >= self.p_valid:
                dut.s_axis_tvalid.value = 0
                await RisingEdge(dut.clk)
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
    """Passive: samples m_axis handshakes and also drives random tready."""

    def build_phase(self):
        self.ap = uvm_analysis_port("ap", self)
        self.p_ready = ConfigDB().get(self, "", "p_ready")

    def start_of_simulation_phase(self):
        self.dut = cocotb.top

    async def run_phase(self):
        dut = self.dut
        rows = []
        while True:
            ready = int(random.random() < self.p_ready)
            dut.m_axis_tready.value = ready
            await ReadOnly()
            if ready and int(dut.m_axis_tvalid.value) == 1:
                rows.append(unpack_row(int(dut.m_axis_tdata.value), N))
                if int(dut.m_axis_tlast.value) == 1:
                    if len(rows) != N:
                        self.logger.error(f"tlast after {len(rows)} rows, expected {N}")
                    self.ap.write(np.array(rows, dtype=np.int64))
                    rows = []
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
class SaCoverage(uvm_subscriber):
    BINS = {"K=1": lambda k: k == 1, "K 2-8": lambda k: 2 <= k <= 8,
            "K 9-32": lambda k: 9 <= k <= 32, "K 33-64": lambda k: 33 <= k <= 64}

    def build_phase(self):
        self.hits = {b: 0 for b in self.BINS}
        self.hits["has -128"] = 0
        self.hits["has 127"] = 0

    def write(self, item):
        K = item.A.shape[1]
        for b, f in self.BINS.items():
            if f(K):
                self.hits[b] += 1
        if (item.A == -128).any() or (item.B == -128).any():
            self.hits["has -128"] += 1
        if (item.A == 127).any() or (item.B == 127).any():
            self.hits["has 127"] += 1

    def report_phase(self):
        covered = sum(1 for v in self.hits.values() if v > 0)
        self.logger.info(f"Coverage: {covered}/{len(self.hits)} bins hit -> {self.hits}")
        missing = [b for b, v in self.hits.items() if v == 0]
        if missing:
            self.logger.warning(f"Uncovered bins: {missing}")


# ---------------------------------------------------------------------- env
class SaEnv(uvm_env):
    def build_phase(self):
        self.agent = SaAgent("agent", self)
        self.scoreboard = SaScoreboard("scoreboard", self)
        self.coverage = SaCoverage("coverage", self)

    def connect_phase(self):
        self.agent.driver.ap.connect(self.scoreboard.exp_export)
        self.agent.driver.ap.connect(self.coverage.analysis_export)
        self.agent.monitor.ap.connect(self.scoreboard.act_export)


# --------------------------------------------------------------------- tests
class SaBaseTest(uvm_test):
    P_VALID = 1.0
    P_READY = 1.0
    SEQS = (RandomSeq, CornerSeq)

    def build_phase(self):
        random.seed(1234)
        np.random.seed(1234)
        ConfigDB().set(None, "*", "p_valid", self.P_VALID)
        ConfigDB().set(None, "*", "p_ready", self.P_READY)
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
