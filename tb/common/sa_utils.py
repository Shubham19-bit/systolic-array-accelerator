"""
sa_utils.py - Shared helpers for the cocotb and pyuvm testbenches.

Written for cocotb 1.9.x (see requirements.txt).
"""
from __future__ import annotations

import os
import random

import numpy as np
from cocotb.clock import Clock
from cocotb.triggers import ReadOnly, RisingEdge

DW = 8
AW = 32


def params():
    """Array size comes from the Makefile (exported as SA_N)."""
    return int(os.environ.get("SA_N", "4"))


def pipe_param():
    """PE pipelining (0/1) from the Makefile (exported as SA_PIPE)."""
    return int(os.environ.get("SA_PIPE", "1"))


def drain_cycles(n: int, pipe: int) -> int:
    """DRAIN length of sa_top: minimum 2N-2 (1 for N=1), +1 if the PE is pipelined."""
    return (2 * n - 2 if n > 1 else 1) + pipe


# ----------------------------------------------------------------- bit packing
def to_unsigned(v: int, width: int) -> int:
    return int(v) & ((1 << width) - 1)


def to_signed(v: int, width: int) -> int:
    v = int(v) & ((1 << width) - 1)
    return v - (1 << width) if v >> (width - 1) else v


def pack_beat(a_col, b_row, n: int) -> int:
    """Beat k = column k of A (low half) + row k of B (high half)."""
    word = 0
    for i, a in enumerate(a_col):
        word |= to_unsigned(a, DW) << (i * DW)
    for j, b in enumerate(b_row):
        word |= to_unsigned(b, DW) << ((n + j) * DW)
    return word


def unpack_row(word: int, n: int):
    return [to_signed(word >> (j * AW), AW) for j in range(n)]


def golden(A: np.ndarray, B: np.ndarray) -> np.ndarray:
    """Reference model: int64 matmul wrapped to AW bits (two's complement)."""
    C = (A.astype(np.int64) @ B.astype(np.int64))
    m = 1 << AW
    C = C & (m - 1)
    return np.where(C >= (m >> 1), C - m, C)


# ------------------------------------------------------------- DUT utilities
async def start_clock_and_reset(dut, period_ns: int = 10, cycles: int = 5):
    """Start the clock (call once per test) and apply reset."""
    import cocotb
    cocotb.start_soon(Clock(dut.clk, period_ns, units="ns").start())
    await reset_dut(dut, cycles)


async def reset_dut(dut, cycles: int = 5):
    """Apply an active-low reset for `cycles` clocks and idle all inputs."""
    dut.rst_n.value = 0
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tdata.value = 0
    dut.s_axis_tlast.value = 0
    dut.m_axis_tready.value = 0
    for _ in range(cycles):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def send_job(dut, A: np.ndarray, B: np.ndarray, n: int,
                   p_valid: float = 1.0, rng: random.Random | None = None):
    """AXI4-Stream master: drive K beats (with optional random idle cycles)."""
    rng = rng or random.Random()
    K = A.shape[1]
    for k in range(K):
        while rng.random() >= p_valid:          # random bubble
            dut.s_axis_tvalid.value = 0
            await RisingEdge(dut.clk)
        dut.s_axis_tvalid.value = 1
        dut.s_axis_tdata.value = pack_beat(A[:, k], B[k, :], n)
        dut.s_axis_tlast.value = int(k == K - 1)
        while True:
            await ReadOnly()
            accepted = int(dut.s_axis_tready.value) == 1
            await RisingEdge(dut.clk)
            if accepted:
                break
    dut.s_axis_tvalid.value = 0
    dut.s_axis_tlast.value = 0


async def recv_job(dut, n: int, p_ready: float = 1.0,
                   rng: random.Random | None = None, timeout: int = 100000):
    """AXI4-Stream slave: collect N result rows (with random back-pressure)."""
    rng = rng or random.Random()
    rows = []
    for _ in range(timeout):
        ready = int(rng.random() < p_ready)
        dut.m_axis_tready.value = ready
        await ReadOnly()
        fire = ready and int(dut.m_axis_tvalid.value) == 1
        if fire:
            rows.append(unpack_row(int(dut.m_axis_tdata.value), n))
            last = int(dut.m_axis_tlast.value) == 1
            expect_last = len(rows) == n
            assert last == expect_last, \
                f"tlast={last} on row {len(rows) - 1} (expected {expect_last})"
        await RisingEdge(dut.clk)
        if len(rows) == n:
            dut.m_axis_tready.value = 0
            return np.array(rows, dtype=np.int64)
    raise TimeoutError("result stream did not complete")
