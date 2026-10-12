"""
test_sa_layer.py - cocotb tests for rtl/sa_layer.sv (systolic GEMM + INT32 -> ReLU -> INT8).

Run:  make layer            (= make -C tb/cocotb TOP=sa_layer MODULE=test_sa_layer)

End-to-end: random A, B -> golden GEMM (NumPy, 32-bit) -> quantize() must equal the INT8 rows
that come out of the DUT. The requantization details (every shift, ties, saturation) are
tested on the stage alone in test_quant.py; here the point is the integration: scale
choices that exercise saturation, ReLU, back-pressure through both stages, and latency.
"""
from __future__ import annotations

import random

import cocotb
import numpy as np
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

from sa_utils import (golden, params, pipe_param, qstages_param, quantize, send_job,
                      start_clock_and_reset, unpack_row8)

N = params()
PIPE = pipe_param()
QSTAGES = qstages_param()


async def recv_quant_job(dut, p_ready: float, rng: random.Random, timeout: int = 100000):
    """AXI4-Stream sink: collect N INT8 rows (with random back-pressure)."""
    rows = []
    for _ in range(timeout):
        ready = int(rng.random() < p_ready)
        dut.m_axis_tready.value = ready
        await ReadOnly()
        if ready and int(dut.m_axis_tvalid.value) == 1:
            rows.append(unpack_row8(int(dut.m_axis_tdata.value), N))
            last = int(dut.m_axis_tlast.value) == 1
            assert last == (len(rows) == N), f"tlast={last} on row {len(rows) - 1}"
        await RisingEdge(dut.clk)
        if len(rows) == N:
            dut.m_axis_tready.value = 0
            return np.array(rows, dtype=np.int64)
    raise TimeoutError("result stream did not complete")


async def run_layer(dut, A, B, shift, relu, p_valid=1.0, p_ready=1.0, rng=None):
    rng = rng or random.Random()
    dut.cfg_shift.value = shift
    dut.cfg_relu.value = int(relu)
    sender = cocotb.start_soon(send_job(dut, A, B, N, p_valid, rng))
    got = await recv_quant_job(dut, p_ready, rng)
    await sender
    exp = quantize(golden(A, B), shift, relu)
    if not np.array_equal(got, exp):
        raise AssertionError(f"shift={shift} relu={relu}\nA=\n{A}\nB=\n{B}\ngot=\n{got}\nexp=\n{exp}")


def auto_shift(A, B) -> int:
    """Scale that just fits the largest |C| into 8 bits (what a quantizer would calibrate)."""
    return min(31, max(0, int(np.abs(golden(A, B)).max()).bit_length() - 7))


@cocotb.test()
async def test_layer_random(dut):
    """Random GEMMs with calibrated, too-small and too-large shifts, ReLU on and off."""
    await start_clock_and_reset(dut)
    rng = np.random.default_rng(11)
    prng = random.Random(11)
    for _ in range(40):
        K = int(rng.integers(1, 49))
        A = rng.integers(-128, 128, size=(N, K))
        B = rng.integers(-128, 128, size=(K, N))
        shift = int(np.clip(auto_shift(A, B) + rng.integers(-2, 3), 0, 31))
        await run_layer(dut, A, B, shift, bool(rng.integers(0, 2)), rng=prng)


@cocotb.test()
async def test_layer_saturation(dut):
    """Shift 0 with large products: everything saturates to +127 / -128, ReLU gives 0."""
    await start_clock_and_reset(dut)
    prng = random.Random(12)
    K = 16
    pos_a = np.full((N, K), 100, dtype=np.int64)
    pos_b = np.full((K, N), 100, dtype=np.int64)
    await run_layer(dut, pos_a, pos_b, 0, False, rng=prng)       # C = 160000 -> 127
    await run_layer(dut, pos_a, -pos_b, 0, False, rng=prng)      # C = -160000 -> -128
    await run_layer(dut, pos_a, -pos_b, 0, True, rng=prng)       # ReLU -> 0
    await run_layer(dut, pos_a, pos_b, 12, False, rng=prng)      # 160000 / 4096 = 39.06 -> 39


@cocotb.test()
async def test_layer_stalls(dut):
    """Input bubbles and output back-pressure through both stages."""
    await start_clock_and_reset(dut)
    rng = np.random.default_rng(13)
    prng = random.Random(13)
    for _ in range(25):
        K = int(rng.integers(1, 33))
        A = rng.integers(-128, 128, size=(N, K))
        B = rng.integers(-128, 128, size=(K, N))
        await run_layer(dut, A, B, auto_shift(A, B), bool(rng.integers(0, 2)),
                        p_valid=0.6, p_ready=0.4, rng=prng)


@cocotb.test()
async def test_layer_latency(dut):
    """The quantization stage adds exactly QSTAGES cycles to the GEMM latency K + DRAIN + N."""
    await start_clock_and_reset(dut)
    seen = {"first_in": None, "last_out": None}

    async def watch():
        cycle = 0
        while True:
            await ReadOnly()
            if seen["first_in"] is None and int(dut.s_axis_tvalid.value) \
                    and int(dut.s_axis_tready.value):
                seen["first_in"] = cycle
            if int(dut.m_axis_tvalid.value) and int(dut.m_axis_tready.value):
                seen["last_out"] = cycle
            cycle += 1
            await RisingEdge(dut.clk)

    watcher = cocotb.start_soon(watch())
    rng = np.random.default_rng(14)
    K = 10
    A = rng.integers(-128, 128, size=(N, K))
    B = rng.integers(-128, 128, size=(K, N))
    await run_layer(dut, A, B, auto_shift(A, B), False, rng=random.Random(14))
    await ClockCycles(dut.clk, 2)
    watcher.kill()
    drain = (2 * N - 2 if N > 1 else 1) + PIPE
    measured = seen["last_out"] - seen["first_in"] + 1
    assert measured == K + drain + N + QSTAGES, \
        f"{measured} != K+DRAIN+N+QSTAGES = {K + drain + N + QSTAGES}"
    assert int(dut.last_job_cycles.value) == K + drain + N, "sa_top latency counter changed"
