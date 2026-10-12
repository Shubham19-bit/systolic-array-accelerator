"""
test_sa_gemm.py - cocotb tests for rtl/sa_gemm.sv (tiled GEMM with on-chip operand buffers).

Run:  make gemm             (= make -C tb/cocotb TOP=sa_gemm MODULE=test_sa_gemm)

The host side of the tests plays the DMA: it pads A and B with zeros to multiples of N, writes
the operand words into the two memories, sets the job size and pulses `start`. The result stream
(N rows per tile, tiles in (ib, jb) order) is reassembled and compared with the NumPy golden
model. Cycle counts are compared with the closed form  tiles * (K + DRAIN + N + 1) + 1.
"""
from __future__ import annotations

import json
import os
import random

import cocotb
import numpy as np
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

from sa_utils import (DW, drain_cycles, gemm_params, golden, params, pipe_param,
                      start_clock_and_reset, to_unsigned, unpack_row)

N = params()
PIPE = pipe_param()
MAXK, MAXMB, MAXNB = gemm_params()
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "results")


def blocks(x: int) -> int:
    return -(-x // N)


def fit(mb: int, nb: int, K: int) -> tuple[int, int, int]:
    """Clip a (row blocks, column blocks, K) shape to the capacity this DUT was built with."""
    return min(mb, MAXMB), min(nb, MAXNB), min(K, MAXK)


def shapes(*cases) -> list[tuple[int, int, int]]:
    """fit() every case and drop duplicates (small buffers make several cases identical)."""
    return list(dict.fromkeys(fit(*c) for c in cases))


def word(vals) -> int:
    """N signed INT8 values -> one memory word (lane i in bits [i*DW +: DW])."""
    w = 0
    for i, v in enumerate(vals):
        w |= to_unsigned(v, DW) << (i * DW)
    return w


def expected_cycles(mb: int, nb: int, K: int) -> int:
    return mb * nb * (K + drain_cycles(N, PIPE) + N + 1) + 1


async def idle(dut):
    dut.start.value = 0
    dut.a_we.value = 0
    dut.b_we.value = 0
    dut.cfg_k.value = 1
    dut.cfg_mb.value = 1
    dut.cfg_nb.value = 1
    dut.m_axis_tready.value = 0


async def load_operands(dut, A, B):
    """Write A and B into the on-chip memories (one N-byte word per cycle).
    Returns (mb, nb, words written)."""
    M, K = A.shape
    Nc = B.shape[1]
    mb, nb = blocks(M), blocks(Nc)
    Ap = np.zeros((mb * N, K), dtype=np.int64)
    Ap[:M] = A
    Bp = np.zeros((K, nb * N), dtype=np.int64)
    Bp[:, :Nc] = B
    words = 0
    for ib in range(mb):
        for k in range(K):
            dut.a_we.value = 1
            dut.a_waddr.value = ib * K + k
            dut.a_wdata.value = word(Ap[ib * N:(ib + 1) * N, k])
            await RisingEdge(dut.clk)
            words += 1
    dut.a_we.value = 0
    for jb in range(nb):
        for k in range(K):
            dut.b_we.value = 1
            dut.b_waddr.value = jb * K + k
            dut.b_wdata.value = word(Bp[k, jb * N:(jb + 1) * N])
            await RisingEdge(dut.clk)
            words += 1
    dut.b_we.value = 0
    return mb, nb, words


async def run_gemm(dut, K, mb, nb, p_ready=1.0, rng=None, start_again_at=None, timeout=200000):
    """Start a job on the loaded operands and collect its result rows.
    Returns (padded C, gemm_cycles, beats_fed)."""
    rng = rng or random.Random(0)
    dut.cfg_k.value = K
    dut.cfg_mb.value = mb
    dut.cfg_nb.value = nb
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0

    total = mb * nb * N
    rows, prev = [], None
    for cycle in range(timeout):
        if start_again_at is not None and cycle == start_again_at:
            dut.start.value = 1              # a start while busy must be ignored
        elif start_again_at is not None and cycle == start_again_at + 1:
            dut.start.value = 0
        ready = rng.random() < p_ready
        dut.m_axis_tready.value = int(ready)
        await ReadOnly()
        valid = int(dut.m_axis_tvalid.value) == 1
        data, last = int(dut.m_axis_tdata.value), int(dut.m_axis_tlast.value)
        if prev is not None and prev[0] and not prev[1]:
            assert valid and (data, last) == prev[2:], \
                f"cycle {cycle}: output changed while stalled by back-pressure"
        prev = (valid, ready, data, last)
        assert int(dut.busy.value) == 1, f"busy dropped before the last row (cycle {cycle})"
        assert int(dut.done.value) == 0, "done before the last row"
        if valid and ready:
            rows.append(unpack_row(data, N))
            assert last == (len(rows) == total), \
                f"tlast={last} on row {len(rows) - 1} of {total}"
        if len(rows) == total:
            break
        await RisingEdge(dut.clk)
    else:
        raise TimeoutError("GEMM did not complete")

    await RisingEdge(dut.clk)                # the cycle after the last handshake
    dut.m_axis_tready.value = 0
    dut.start.value = 0
    await ReadOnly()
    assert int(dut.done.value) == 1 and int(dut.busy.value) == 0, "done pulse / busy wrong"
    cycles, beats = int(dut.gemm_cycles.value), int(dut.beats_fed.value)
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert int(dut.done.value) == 0, "done is not a one-cycle pulse"
    await RisingEdge(dut.clk)

    rows = np.array(rows, dtype=np.int64)
    C = np.zeros((mb * N, nb * N), dtype=np.int64)
    for ib in range(mb):
        for jb in range(nb):
            t = ib * nb + jb
            C[ib * N:(ib + 1) * N, jb * N:(jb + 1) * N] = rows[t * N:(t + 1) * N]
    return C, cycles, beats


def random_operands(rng, M, K, Nc):
    return (rng.integers(-128, 128, size=(M, K)), rng.integers(-128, 128, size=(K, Nc)))


async def check_gemm(dut, A, B, **kw):
    """load + run + compare. Returns (cycles, beats, words, mb, nb)."""
    mb, nb, words = await load_operands(dut, A, B)
    C, cycles, beats = await run_gemm(dut, A.shape[1], mb, nb, **kw)
    M, Nc = A.shape[0], B.shape[1]
    exp = golden(A, B)
    assert np.array_equal(C[:M, :Nc], exp), f"mismatch\ngot=\n{C[:M, :Nc]}\nexp=\n{exp}"
    assert not C[M:].any() and not C[:, Nc:].any(), "padding rows/columns are not zero"
    return cycles, beats, words, mb, nb


async def start_up(dut):
    await start_clock_and_reset(dut)
    await idle(dut)
    await ClockCycles(dut.clk, 2)


@cocotb.test()
async def test_single_tile(dut):
    """One output tile (mb = nb = 1)."""
    await start_up(dut)
    rng = np.random.default_rng(1)
    A, B = random_operands(rng, N, min(5, MAXK), N)
    await check_gemm(dut, A, B, rng=random.Random(1))


@cocotb.test()
async def test_multi_tile(dut):
    """Several row and column blocks: the tiling loops and the reuse of buffered tiles."""
    await start_up(dut)
    rng = np.random.default_rng(2)
    for mb, nb, K in shapes((3, 2, 12), (2, 4, 7), (4, 1, 9)):
        A, B = random_operands(rng, mb * N, K, nb * N)
        await check_gemm(dut, A, B, rng=random.Random(2))


@cocotb.test()
async def test_non_multiple_dims(dut):
    """M and Nc that are not multiples of N: zero padding, the padded part stays zero."""
    await start_up(dut)
    rng = np.random.default_rng(3)
    for M, K, Nc in ((min(N + 1, MAXMB * N), min(7, MAXK), min(2 * N - 1, MAXNB * N)),
                     (min(2 * N + (N > 1), MAXMB * N), min(4, MAXK), min(N + 1, MAXNB * N)),
                     (1, min(3, MAXK), 1)):
        A, B = random_operands(rng, M, K, Nc)
        await check_gemm(dut, A, B, rng=random.Random(3))


@cocotb.test()
async def test_cycle_count_exact(dut):
    """gemm_cycles == tiles * (K + DRAIN + N + 1) + 1 with no stalls (written to results/ so
    that perf_model.py --validate can check its tile model against whole GEMMs)."""
    await start_up(dut)
    rng = np.random.default_rng(4)
    log = []
    cases = shapes((1, 1, 1), (1, 1, 5), (1, 3, 8), (3, 1, 8), (2, 2, 12), (4, 4, 16),
                   (MAXMB, MAXNB, MAXK))
    for i, (mb, nb, K) in enumerate(cases):
        trim = N // 2 if i % 2 else 0               # sometimes a partial last block
        M, Nc = mb * N - trim, nb * N - trim
        A, B = random_operands(rng, M, K, Nc)
        cycles, beats, _, mb_, nb_ = await check_gemm(dut, A, B)
        assert (mb_, nb_) == (mb, nb)
        assert cycles == expected_cycles(mb, nb, K), \
            f"mb={mb} nb={nb} K={K}: {cycles} cycles != {expected_cycles(mb, nb, K)}"
        assert beats == mb * nb * K, f"beats_fed {beats} != {mb * nb * K}"
        log.append({"N": N, "M": M, "K": K, "Nc": Nc, "pipe": PIPE, "cycles": cycles})
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, f"sim_gemm_N{N}.json"), "w") as f:
        json.dump(log, f, indent=1)


@cocotb.test()
async def test_operand_reuse(dut):
    """The point of the buffers: operands cross the external interface once, but the array is
    fed mb*nb*K beats. External bytes (mb+nb)*K*N vs fed bytes mb*nb*K*2N."""
    await start_up(dut)
    rng = np.random.default_rng(5)
    mb, nb, K = fit(4, 4, 16)
    A, B = random_operands(rng, mb * N, K, nb * N)
    _, beats, words, _, _ = await check_gemm(dut, A, B)
    external, fed = words * N, beats * 2 * N
    assert external == (mb + nb) * K * N and fed == mb * nb * K * 2 * N
    dut._log.info(f"mb={mb} nb={nb} K={K}: {external} B loaded once, {fed} B fed to the array "
                  f"-> {fed / external:.2f}x reuse (2*mb*nb/(mb+nb) = {2 * mb * nb / (mb + nb):.2f})")
    assert fed * (mb + nb) == external * 2 * mb * nb


@cocotb.test()
async def test_backpressure(dut):
    """Random output back-pressure: correct data, output stable while stalled."""
    await start_up(dut)
    rng = np.random.default_rng(6)
    for mb, nb, K in shapes((2, 3, 6), (3, 2, 11)):
        A, B = random_operands(rng, mb * N, K, nb * N)
        await check_gemm(dut, A, B, p_ready=0.3, rng=random.Random(6))


@cocotb.test()
async def test_reload_and_rerun(dut):
    """Buffers keep their contents: re-run without reloading, then run a smaller job
    (fewer row blocks) on the same buffers; finally load new operands."""
    await start_up(dut)
    rng = np.random.default_rng(7)
    mb, nb, K = fit(3, 2, 10)
    A, B = random_operands(rng, mb * N, K, nb * N)
    mb_, nb_, _ = await load_operands(dut, A, B)
    for _ in range(2):                                    # same job twice, no reload
        C, _, _ = await run_gemm(dut, K, mb_, nb_)
        assert np.array_equal(C, golden(A, B))
    if mb > 1:                                            # sub-problem: first mb-1 row blocks
        C, _, _ = await run_gemm(dut, K, mb_ - 1, nb_)
        assert np.array_equal(C, golden(A[:(mb - 1) * N], B))
    mb2, nb2, K2 = fit(2, 3, 6)                           # new operands, different shape
    A2, B2 = random_operands(rng, mb2 * N, K2, nb2 * N)
    await check_gemm(dut, A2, B2)


@cocotb.test()
async def test_start_ignored_while_busy(dut):
    """A start pulse in the middle of a job is ignored."""
    await start_up(dut)
    rng = np.random.default_rng(8)
    mb, nb, K = fit(2, 2, 9)
    A, B = random_operands(rng, mb * N, K, nb * N)
    await check_gemm(dut, A, B, start_again_at=4)         # the shortest job runs 8+ cycles


@cocotb.test()
async def test_reset_mid_run(dut):
    """Reset in the middle of a job; the next job is still correct."""
    await start_up(dut)
    rng = np.random.default_rng(9)
    mb, nb, K = fit(2, 2, 12)
    A, B = random_operands(rng, mb * N, K, nb * N)
    mb, nb, _ = await load_operands(dut, A, B)
    dut.cfg_k.value, dut.cfg_mb.value, dut.cfg_nb.value = K, mb, nb
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    await ClockCycles(dut.clk, 4)
    assert int(dut.busy.value) == 1
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 3)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 3)
    assert int(dut.busy.value) == 0 and int(dut.m_axis_tvalid.value) == 0
    C, _, _ = await run_gemm(dut, K, mb, nb)              # memories were not reset
    assert np.array_equal(C, golden(A, B))


@cocotb.test()
async def test_coverage_matrix(dut):
    """Directed matrix over the interesting corners; every bin must be hit."""
    await start_up(dut)
    rng = np.random.default_rng(10)
    required = {"single tile", "K = 1", "M padded", "Nc padded", "back-pressure", "full capacity"}
    if MAXMB > 1:
        required.add("row blocks > 1")
    if MAXNB > 1:
        required.add("col blocks > 1")
    if MAXK > 1:
        required.add("K = MAXK")
    hit: set[str] = set()
    cases = [(1, 1, 1, 0, 0, 1.0), (1, 1, MAXK, 0, 0, 1.0), (MAXMB, 1, 5, 1, 0, 1.0),
             (1, MAXNB, 5, 0, 1, 0.5), (2, 3, 9, 1, 1, 0.4), (MAXMB, MAXNB, MAXK, 1, 1, 1.0)]
    for mb, nb, K, trim_m, trim_n, p_ready in [(*fit(mb, nb, K), tm, tn, pr)
                                               for mb, nb, K, tm, tn, pr in cases]:
        M = mb * N - (N // 2 if trim_m else 0)
        Nc = nb * N - (N // 2 if trim_n else 0)
        A, B = random_operands(rng, M, K, Nc)
        await check_gemm(dut, A, B, p_ready=p_ready, rng=random.Random(10))
        hit |= {name for cond, name in (
            (mb == 1 and nb == 1, "single tile"), (mb > 1, "row blocks > 1"),
            (nb > 1, "col blocks > 1"), (K == 1, "K = 1"), (K == MAXK, "K = MAXK"),
            (M % N != 0, "M padded"), (Nc % N != 0, "Nc padded"), (p_ready < 1, "back-pressure"),
            (mb == MAXMB and nb == MAXNB and K == MAXK, "full capacity")) if cond}
    missing = required - hit
    assert not missing, f"coverage bins never hit: {sorted(missing)}"
    dut._log.info(f"all {len(required)} coverage bins hit")
