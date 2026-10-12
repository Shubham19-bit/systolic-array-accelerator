"""
test_quant.py - cocotb tests for rtl/quant_relu.sv (INT32 -> ReLU -> INT8 stage).

Run:  make quant            (= make -C tb/cocotb TOP=quant_relu MODULE=test_quant)

Every test streams beats of N 32-bit accumulators through the stage and compares the
INT8 output with tb/common/sa_utils.py:quantize(). run_stream() also checks, on every
cycle, that the output holds while it is stalled by back-pressure (AXI4-Stream rule).
"""
from __future__ import annotations

import random

import cocotb
import numpy as np
from cocotb.triggers import ReadOnly, RisingEdge

from sa_utils import (pack_row32, params, qstages_param, quantize, start_clock_and_reset,
                      unpack_row8)

N = params()
STAGES = qstages_param()
INT32_MAX, INT32_MIN = 2**31 - 1, -(2**31)

# coverage bins that the directed + random stimulus must reach
REQUIRED_BINS = {"saturate high", "saturate low", "in range", "exactly 127", "exactly -128",
                 "relu clamps negative", "rounding tie", "shift 0", "shift 31", "shift other",
                 "relu on", "relu off"}


def edge_values(shift: int) -> list[int]:
    """Accumulator values at the interesting points of the datapath for this shift."""
    vals = [0, 1, -1, 127, 128, -128, -129, 255, 256, 12345, -12345,
            INT32_MAX, INT32_MIN, 2**30, -(2**30)]
    if shift:
        half = 1 << (shift - 1)
        for q in (-129, -128, -1, 0, 1, 126, 127, 128):
            tie = (q << shift) + half          # exactly halfway: rounds up to q + 1
            vals += [tie - 1, tie, tie + 1]
    return [v for v in vals if INT32_MIN <= v <= INT32_MAX]


def random_values(rng: random.Random, count: int) -> list[int]:
    out = []
    for _ in range(count):
        bits = rng.randint(0, 31)
        out.append(rng.randint(max(INT32_MIN, -(1 << bits)), min(INT32_MAX, (1 << bits) - 1)))
    return out


def to_beats(vals: list[int]) -> list[list[int]]:
    vals = vals + [0] * (-len(vals) % N)
    return [vals[i:i + N] for i in range(0, len(vals), N)]


def bins_hit(vals: list[int], shift: int, relu: bool) -> set[str]:
    hits = {f"shift {'0' if shift == 0 else '31' if shift == 31 else 'other'}",
            "relu on" if relu else "relu off"}
    for x in vals:
        xr = max(x, 0) if relu else x
        if relu and x < 0:
            hits.add("relu clamps negative")
        sh = (xr + ((1 << (shift - 1)) if shift else 0)) >> shift
        hits.add("saturate high" if sh > 127 else "saturate low" if sh < -128 else "in range")
        if sh == 127:
            hits.add("exactly 127")
        if sh == -128:
            hits.add("exactly -128")
        if shift and xr % (1 << shift) == 1 << (shift - 1):
            hits.add("rounding tie")
    return hits


async def run_stream(dut, beats, p_valid=1.0, p_ready=1.0, rng=None, timeout=200000,
                     cfg_after=None):
    """Send `beats` (lists of N ints), collect all output beats. Returns
    (output rows, tlast flags, cycle each beat was accepted, cycle each beat was output).
    cfg_after=(shift, relu): driven as soon as the last beat has been accepted, i.e. while
    earlier beats are still inside the pipeline (each beat must keep the cfg it entered with)."""
    rng = rng or random.Random(0)
    sent, pending, prev = 0, False, None
    outs, lasts, in_cycle, out_cycle = [], [], [], []
    for cycle in range(timeout):
        if sent < len(beats) and not pending:
            pending = rng.random() < p_valid          # AXI: once valid, hold until accepted
        dut.s_axis_tvalid.value = int(pending)
        if pending:
            dut.s_axis_tdata.value = pack_row32(beats[sent])
            dut.s_axis_tlast.value = int(sent == len(beats) - 1)
        m_ready = rng.random() < p_ready
        dut.m_axis_tready.value = int(m_ready)
        await ReadOnly()

        s_ready = int(dut.s_axis_tready.value) == 1
        m_valid = int(dut.m_axis_tvalid.value) == 1
        data, last = int(dut.m_axis_tdata.value), int(dut.m_axis_tlast.value)
        if prev is not None and prev[0] and not prev[1]:     # stalled in the previous cycle
            assert m_valid and (data, last) == prev[2:], \
                f"cycle {cycle}: output changed while stalled by back-pressure"
        prev = (m_valid, m_ready, data, last)

        if pending and s_ready:
            in_cycle.append(cycle)
            pending = False
            sent += 1
            late_cfg = cfg_after is not None and sent == len(beats)
        else:
            late_cfg = False
        if m_valid and m_ready:
            outs.append(unpack_row8(data, N))
            lasts.append(last)
            out_cycle.append(cycle)
        if len(outs) == len(beats):
            break
        await RisingEdge(dut.clk)
        if late_cfg:                      # takes effect from the cycle after the last acceptance
            await configure(dut, *cfg_after)
    else:
        raise TimeoutError("stream did not complete")
    await RisingEdge(dut.clk)
    dut.s_axis_tvalid.value = 0
    dut.m_axis_tready.value = 0
    return outs, lasts, in_cycle, out_cycle


def check(outs, lasts, vals, shift, relu, what=""):
    got = np.array(outs).reshape(-1)[:len(vals)]
    exp = quantize(np.array(vals, dtype=np.int64), shift, relu)
    bad = np.nonzero(got != exp)[0]
    assert bad.size == 0, (f"{what} shift={shift} relu={relu}: input {vals[bad[0]]} -> "
                           f"got {got[bad[0]]}, expected {exp[bad[0]]}")
    assert lasts == [0] * (len(lasts) - 1) + [1], f"tlast pattern wrong: {lasts}"


async def configure(dut, shift: int, relu: bool):
    dut.cfg_shift.value = shift
    dut.cfg_relu.value = int(relu)


@cocotb.test()
async def test_edges_all_shifts(dut):
    """Every shift 0..31, ReLU on/off: INT32 extremes, exact rounding ties, saturation
    boundaries, plus random values. All coverage bins must be hit."""
    await start_clock_and_reset(dut)
    rng = random.Random(1)
    hit: set[str] = set()
    for relu in (False, True):
        for shift in range(32):
            vals = edge_values(shift) + random_values(rng, 12)
            await configure(dut, shift, relu)
            outs, lasts, _, _ = await run_stream(dut, to_beats(vals), rng=rng)
            check(outs, lasts, vals, shift, relu, "edges")
            hit |= bins_hit(vals, shift, relu)
    missing = REQUIRED_BINS - hit
    assert not missing, f"coverage bins never hit: {sorted(missing)}"
    dut._log.info(f"all {len(REQUIRED_BINS)} coverage bins hit: {sorted(hit)}")


@cocotb.test()
async def test_backpressure_random(dut):
    """Random input bubbles and output back-pressure; output must hold while stalled."""
    await start_clock_and_reset(dut)
    rng = random.Random(2)
    for _ in range(40):
        shift, relu = rng.randint(0, 31), rng.random() < 0.5
        vals = random_values(rng, rng.randint(1, 60))
        await configure(dut, shift, relu)
        outs, lasts, _, _ = await run_stream(dut, to_beats(vals), p_valid=0.5, p_ready=0.4,
                                             rng=rng)
        check(outs, lasts, vals, shift, relu, "stalls")


@cocotb.test()
async def test_latency_and_throughput(dut):
    """With no stalls: one beat accepted per cycle and every beat appears STAGES cycles later."""
    await start_clock_and_reset(dut)
    rng = random.Random(3)
    vals = random_values(rng, 20 * N)
    await configure(dut, 7, True)
    outs, lasts, in_cycle, out_cycle = await run_stream(dut, to_beats(vals), rng=rng)
    check(outs, lasts, vals, 7, True, "latency")
    assert in_cycle == list(range(in_cycle[0], in_cycle[0] + len(in_cycle))), \
        "not one beat per cycle"
    assert [o - i for i, o in zip(in_cycle, out_cycle)] == [STAGES] * len(in_cycle), \
        f"latency is not exactly {STAGES} cycle(s)"


@cocotb.test()
async def test_cfg_change_while_in_flight(dut):
    """Each beat keeps the configuration it entered with: changing cfg_shift / cfg_relu right
    after the last beat was accepted must not affect beats still inside the pipeline."""
    await start_clock_and_reset(dut)
    rng = random.Random(4)
    for _ in range(20):
        shift, relu = rng.randint(0, 31), rng.random() < 0.5
        vals = random_values(rng, rng.randint(2 * N, 8 * N))
        await configure(dut, shift, relu)
        outs, lasts, _, _ = await run_stream(dut, to_beats(vals), rng=rng,
                                             cfg_after=(rng.randint(0, 31), rng.random() < 0.5))
        check(outs, lasts, vals, shift, relu, "cfg change in flight")
