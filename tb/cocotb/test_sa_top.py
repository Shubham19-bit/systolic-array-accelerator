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
import sys

import cocotb
import numpy as np
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

from sa_utils import (DW, golden, pack_beat, params, recv_job, reset_dut,
                      send_job, start_clock_and_reset, unpack_row)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "model"))
from sa_cycle_model import SystolicArrayModel  # noqa: E402

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


@cocotb.test()
async def test_cycle_accurate_vs_model(dut):
    """RTL vs model/sa_cycle_model.py, compared on EVERY clock cycle.

    Both see identical stimulus (random input bubbles and output back-pressure, in four
    phases, plus garbage on tdata/tlast while tvalid is low). On each cycle, before the
    clock edge, the RTL outputs s_axis_tready, m_axis_tvalid, m_axis_tlast,
    last_job_cycles and (when valid) m_axis_tdata must equal the model's. A latency or
    handshake difference shows up on the exact cycle it first appears, not just as a
    wrong final result.
    """
    await start_clock_and_reset(dut)
    model = SystolicArrayModel(N)
    model.step(False, m_ready=False)    # reset_dut() last edge took CLEAR -> LOAD

    rng = random.Random(5)
    nprng = np.random.default_rng(5)
    jobs = []
    for _ in range(24):
        K = int(nprng.integers(1, 33))
        jobs.append((nprng.integers(-128, 128, size=(N, K)),
                     nprng.integers(-128, 128, size=(K, N))))
    phases = ((1.0, 1.0), (0.5, 0.5), (0.8, 0.3), (0.3, 0.9))   # (p_valid, p_ready)

    job_i, k, pending = 0, 0, False     # next beat to send; AXI: hold valid until accepted
    rtl_rows, model_rows = [], []
    settle = 10                         # idle cycles still compared after the last row
    for cycle in range(200000):
        p_valid = phases[job_i % 4][0]
        p_ready = phases[(len(model_rows) // N) % 4][1]
        if job_i < len(jobs) and not pending:
            pending = rng.random() < p_valid
        s_valid = pending
        m_ready = rng.random() < p_ready

        if s_valid:
            A, B = jobs[job_i]
            a_col, b_row, s_last = A[:, k], B[k, :], k == A.shape[1] - 1
            dut.s_axis_tdata.value = pack_beat(a_col, b_row, N)
            dut.s_axis_tlast.value = int(s_last)
        else:                           # garbage while invalid: the DUT must ignore it
            a_col = b_row = None
            s_last = False
            dut.s_axis_tdata.value = rng.getrandbits(2 * N * DW)
            dut.s_axis_tlast.value = rng.getrandbits(1)
        dut.s_axis_tvalid.value = int(s_valid)
        dut.m_axis_tready.value = int(m_ready)
        await ReadOnly()

        exp = (int(model.s_ready()), int(model.m_valid()), int(model.m_last()))
        got = (int(dut.s_axis_tready.value), int(dut.m_axis_tvalid.value),
               int(dut.m_axis_tlast.value))
        assert got == exp, f"cycle {cycle}: (tready, tvalid, tlast) RTL={got} model={exp}"
        assert int(dut.last_job_cycles.value) == model.last_job_cycles, (
            f"cycle {cycle}: last_job_cycles RTL={int(dut.last_job_cycles.value)} "
            f"model={model.last_job_cycles}")
        if exp[1]:
            rtl_row = np.array(unpack_row(int(dut.m_axis_tdata.value), N))
            assert np.array_equal(rtl_row, model.m_data()), (
                f"cycle {cycle}: tdata RTL={rtl_row} model={model.m_data()}")

        in_fire, out_fire, out_row = model.step(s_valid, a_col, b_row, s_last, m_ready)
        if in_fire:
            pending = False
            k += 1
            if k == jobs[job_i][0].shape[1]:
                job_i, k = job_i + 1, 0
        if out_fire:
            model_rows.append(out_row)
            rtl_rows.append(rtl_row)
        if len(model_rows) == len(jobs) * N:
            settle -= 1
            if settle == 0:
                break
        await RisingEdge(dut.clk)
    else:
        raise AssertionError("jobs did not complete")

    for j, (A, B) in enumerate(jobs):   # the lockstep run also produced the right C
        rows = slice(j * N, (j + 1) * N)
        assert np.array_equal(np.array(rtl_rows[rows]), golden(A, B)), f"job {j} wrong C"
    dut._log.info(f"cycle-accurate match for {cycle + 1} cycles, {len(jobs)} jobs")
