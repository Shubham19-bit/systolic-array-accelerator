"""
sa_cycle_model.py - Cycle-accurate Python mirror of rtl/sa_top.sv.

Every register in the RTL has a counterpart here and is updated with the same
rules on every clock edge. Use it to:
  * reason about timing (skew, drain length, latency) without a simulator,
  * act as a golden *cycle* model (not just a functional model) for the RTL,
  * prototype architecture changes before touching SystemVerilog.

Run `python model/sa_cycle_model.py` for a self-test against NumPy.
"""
from __future__ import annotations

import numpy as np

S_CLEAR, S_LOAD, S_DRAIN, S_OUTPUT = 0, 1, 2, 3


class SystolicArrayModel:
    def __init__(self, n: int = 4, dw: int = 8, aw: int = 32):
        self.n, self.dw, self.aw = n, dw, aw
        self.drain_cyc = 2 * n - 2 if n > 1 else 1
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self):
        n = self.n
        self.state = S_CLEAR
        self.drain_cnt = 0
        self.row_idx = 0
        self.cyc_cnt = 0
        self.last_job_cycles = 0
        # skew registers: lane i holds a list of i samples (index 0 = newest)
        self.skew_a = [[0] * i for i in range(n)]
        self.skew_b = [[0] * i for i in range(n)]
        # PE registers
        self.a_out = np.zeros((n, n), dtype=np.int64)
        self.b_out = np.zeros((n, n), dtype=np.int64)
        self.acc = np.zeros((n, n), dtype=np.int64)

    # ------------------------------------------------------ combinational view
    def s_ready(self) -> bool:
        return self.state == S_LOAD

    def m_valid(self) -> bool:
        return self.state == S_OUTPUT

    def m_data(self) -> np.ndarray:
        return self.acc[self.row_idx].copy()

    def m_last(self) -> bool:
        return self.state == S_OUTPUT and self.row_idx == self.n - 1

    def _wrap(self, x):
        """Two's-complement wrap to the accumulator width (as hardware does)."""
        m = 1 << self.aw
        x = np.asarray(x, dtype=np.int64) & (m - 1)
        return np.where(x >= (m >> 1), x - m, x)

    # ------------------------------------------------------------- clock edge
    def step(self, s_valid: bool, a_col=None, b_row=None, s_last: bool = False,
             m_ready: bool = False):
        """Advance one clock. Returns (in_fire, out_fire, out_row or None)."""
        n = self.n
        in_fire = s_valid and self.s_ready()
        out_fire = self.m_valid() and m_ready
        out_row = self.m_data() if out_fire else None
        out_last = self.m_last()

        en = in_fire or self.state == S_DRAIN
        clr = self.state == S_CLEAR

        a_raw = list(a_col) if in_fire else [0] * n
        b_raw = list(b_row) if in_fire else [0] * n

        # skewed operands seen by the array this cycle (combinational)
        a_skew = [a_raw[0]] + [self.skew_a[i][-1] for i in range(1, n)]
        b_skew = [b_raw[0]] + [self.skew_b[i][-1] for i in range(1, n)]

        # operand entering each PE this cycle (combinational)
        a_in = np.zeros((n, n), dtype=np.int64)
        b_in = np.zeros((n, n), dtype=np.int64)
        for i in range(n):
            for j in range(n):
                a_in[i, j] = a_skew[i] if j == 0 else self.a_out[i, j - 1]
                b_in[i, j] = b_skew[j] if i == 0 else self.b_out[i - 1, j]

        # ---- register updates (non-blocking semantics) ----
        if clr:
            self.skew_a = [[0] * i for i in range(n)]
            self.skew_b = [[0] * i for i in range(n)]
            self.a_out[:] = 0
            self.b_out[:] = 0
            self.acc[:] = 0
        elif en:
            for i in range(1, n):
                self.skew_a[i] = [a_raw[i]] + self.skew_a[i][:-1]
                self.skew_b[i] = [b_raw[i]] + self.skew_b[i][:-1]
            self.acc = self._wrap(self.acc + a_in * b_in)
            self.a_out = a_in
            self.b_out = b_in

        if self.state == S_CLEAR:
            self.drain_cnt = self.row_idx = self.cyc_cnt = 0
            self.state = S_LOAD
        elif self.state == S_LOAD:
            if in_fire or self.cyc_cnt != 0:
                self.cyc_cnt += 1
            if in_fire and s_last:
                self.state = S_DRAIN
        elif self.state == S_DRAIN:
            self.cyc_cnt += 1
            if self.drain_cnt == self.drain_cyc - 1:
                self.drain_cnt = 0
                self.state = S_OUTPUT
            else:
                self.drain_cnt += 1
        elif self.state == S_OUTPUT:
            self.cyc_cnt += 1
            if out_fire:
                if out_last:
                    self.last_job_cycles = self.cyc_cnt
                    self.state = S_CLEAR
                else:
                    self.row_idx += 1

        return in_fire, out_fire, out_row


def run_job(model: SystolicArrayModel, A: np.ndarray, B: np.ndarray,
            rng: np.random.Generator | None = None,
            p_valid: float = 1.0, p_ready: float = 1.0, max_cycles: int = 100000):
    """Stream one job through the model with optional random stalls."""
    n = model.n
    K = A.shape[1]
    k = 0
    rows = []
    rng = rng or np.random.default_rng(0)
    for _ in range(max_cycles):
        s_valid = k < K and rng.random() < p_valid
        m_ready = rng.random() < p_ready
        in_fire, out_fire, out_row = model.step(
            s_valid,
            A[:, k] if k < K else None,
            B[k, :] if k < K else None,
            s_last=(k == K - 1),
            m_ready=m_ready,
        )
        if in_fire:
            k += 1
        if out_fire:
            rows.append(out_row)
            if len(rows) == n:
                return np.array(rows)
    raise RuntimeError("job did not complete")


def _self_test():
    rng = np.random.default_rng(1)
    for n in (1, 2, 3, 4, 8):
        m = SystolicArrayModel(n)
        for trial in range(30):
            K = int(rng.integers(1, 40))
            A = rng.integers(-128, 128, size=(n, K))
            B = rng.integers(-128, 128, size=(K, n))
            stalls = trial % 2 == 1
            C = run_job(m, A, B, rng,
                        p_valid=0.6 if stalls else 1.0,
                        p_ready=0.5 if stalls else 1.0)
            assert np.array_equal(C, A @ B), (n, K, trial)
            if not stalls:
                assert m.last_job_cycles == K + m.drain_cyc + n, (m.last_job_cycles, K, n)
        # extreme values
        K = 64
        A = np.full((n, K), -128)
        B = np.full((K, n), -128)
        assert np.array_equal(run_job(m, A, B, rng), A @ B)
        print(f"N={n}: PASS (30 random jobs + extreme-value job)")
    print("Cycle model self-test PASSED. Latency (no stalls) = K + DRAIN + N cycles (DRAIN = 2N-2, or 1 for N=1).")


if __name__ == "__main__":
    _self_test()
