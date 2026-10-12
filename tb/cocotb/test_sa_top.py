"""
test_sa_top.py - cocotb tests for the AXI4-Stream systolic-array GEMM engine.

Run:  make            (Icarus, N=4)
      make N=8        (8x8 array)
      make SIM=verilator WAVES=1
"""
from __future__ import annotations

import json
import os
import random

import cocotb
import numpy as np
from cocotb.triggers import ClockCycles

from sa_utils import (golden, params, recv_job, reset_dut, send_job,
                      start_clock_and_reset)

N = params()
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "results")


async def run_and_check(dut, A, B, p_valid=1.0, p_ready=1.0, rng=None):
    rng = rng or random.Random()
    sender = cocotb.start_soon(send_job(dut, A, B, N, p_valid, rng))
    C = await recv_job(dut, N, p_ready, rng)
    await sender
    exp = golden(A, B)
    if not np.array_equal(C, exp):
        raise AssertionError(f"Mismatch\nA=\n{A}\nB=\n{B}\ngot=\n{C}\nexp=\n{exp}")
    return C


@cocotb.test()
async def test_identity(dut):
    """A * I = A (K = N)."""
    await start_clock_and_reset(dut)
    rng = np.random.default_rng(0)
    A = rng.integers(-128, 128, size=(N, N))
    B = np.eye(N, dtype=np.int64)
    await run_and_check(dut, A, B)


@cocotb.test()
async def test_k_equals_one(dut):
    """Minimum depth: a single outer product."""
    await start_clock_and_reset(dut)
    rng = np.random.default_rng(1)
    A = rng.integers(-128, 128, size=(N, 1))
    B = rng.integers(-128, 128, size=(1, N))
    await run_and_check(dut, A, B)


@cocotb.test()
async def test_extreme_values(dut):
    """-128 * -128 accumulated over K = 64 (largest positive products)."""
    await start_clock_and_reset(dut)
    K = 64
    A = np.full((N, K), -128, dtype=np.int64)
    B = np.full((K, N), -128, dtype=np.int64)
    await run_and_check(dut, A, B)
    A = np.full((N, K), 127, dtype=np.int64)
    await run_and_check(dut, A, B)          # most negative products


@cocotb.test()
async def test_random_back_to_back(dut):
    """50 random jobs back to back, no stalls; also checks latency = K + DRAIN + N."""
    await start_clock_and_reset(dut)
    rng = np.random.default_rng(2)
    perf = []
    for _ in range(50):
        K = int(rng.integers(1, 65))
        A = rng.integers(-128, 128, size=(N, K))
        B = rng.integers(-128, 128, size=(K, N))
        await run_and_check(dut, A, B)
        await ClockCycles(dut.clk, 1)
        cycles = int(dut.last_job_cycles.value)
        drain = 2 * N - 2 if N > 1 else 1          # minimum drain, see sa_top.sv
        expected = K + drain + N
        assert cycles == expected, f"latency {cycles} != K+DRAIN+N = {expected}"
        perf.append({"N": N, "K": K, "cycles": cycles})

    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, f"sim_cycles_N{N}.json"), "w") as f:
        json.dump(perf, f, indent=1)
    dut._log.info("latency check passed for 50 jobs (cycles = K + DRAIN + N)")


@cocotb.test()
async def test_random_stalls_and_backpressure(dut):
    """Random input bubbles (valid) and output back-pressure (ready)."""
    await start_clock_and_reset(dut)
    rng = np.random.default_rng(3)
    prng = random.Random(3)
    for _ in range(40):
        K = int(rng.integers(1, 40))
        A = rng.integers(-128, 128, size=(N, K))
        B = rng.integers(-128, 128, size=(K, N))
        await run_and_check(dut, A, B, p_valid=0.6, p_ready=0.4, rng=prng)


@cocotb.test()
async def test_reset_mid_job(dut):
    """Assert reset in the middle of a job; the next job must still be correct."""
    await start_clock_and_reset(dut)
    rng = np.random.default_rng(4)
    A = rng.integers(-128, 128, size=(N, 32))
    B = rng.integers(-128, 128, size=(32, N))
    sender = cocotb.start_soon(send_job(dut, A, B, N))
    await ClockCycles(dut.clk, 10)
    sender.kill()
    await reset_dut(dut)
    await run_and_check(dut, A, B)
