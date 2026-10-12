# Systolic-Array INT8 GEMM Accelerator

A parameterized **N×N output-stationary systolic array** for signed INT8 matrix
multiplication with an **AXI4-Stream** interface, verified with **cocotb + pyuvm**,
checked with **formal properties (SymbiYosys)**, synthesized with **Yosys** on the
**SkyWater 130 nm** library, and analysed with a **cycle-accurate model** and a
**roofline performance model**.

> Status: work in progress. See [`PATHWAY.md`](PATHWAY.md) for the plan and progress.

---

## Architecture

```
            B[k][0]  B[k][1]  B[k][2]  B[k][3]       (top edge, skewed by column)
               |        |        |        |
A[0][k] ---> [PE00]-->[PE01]-->[PE02]-->[PE03]
               |        |        |        |
A[1][k] -D-> [PE10]-->[PE11]-->[PE12]-->[PE13]       D = one-cycle skew register
               |        |        |        |
A[2][k] -DD> [PE20]-->[PE21]-->[PE22]-->[PE23]       each PE:  acc += a * b
               |        |        |        |                    forward a ->, b v
A[3][k] DDD> [PE30]-->[PE31]-->[PE32]-->[PE33]
```

* **Output-stationary:** each PE keeps one element of C in its accumulator.
* **Skew buffers:** row *i* / column *j* are delayed *i* / *j* cycles so that
  `A[i][k]` and `B[k][j]` meet in `PE(i,j)`.
* **Global enable:** input bubbles (`tvalid=0`) freeze the whole pipeline, so
  stalls never corrupt results.

### Interface

| Port | Width | Meaning |
|---|---|---|
| `s_axis_tdata` | `2·N·8` | beat *k*: column *k* of A (low half) + row *k* of B (high half) |
| `s_axis_tlast` | 1 | last beat of the job (k = K−1) |
| `m_axis_tdata` | `N·32` | one row of C per beat |
| `m_axis_tlast` | 1 | last row |
| `last_job_cycles` | 32 | measured latency of the previous job |

### FSM

`CLEAR → LOAD (K beats) → DRAIN (2N-2 cycles, 1 if N=1) → OUTPUT (N rows) → CLEAR`

Latency without stalls: **K + DRAIN + N cycles** = K + 3N - 2 for N >= 2 (verified in simulation).

---

## Repository layout

```
rtl/            pe.sv, skew_buffer.sv, systolic_array.sv, sa_top.sv (+ formal props)
model/          sa_cycle_model.py   cycle-accurate Python mirror of the RTL
tb/common/      sa_utils.py         packing, golden model, AXI-Stream driver/monitor
tb/cocotb/      test_sa_top.py      directed + random + stall/back-pressure tests
tb/pyuvm/       test_sa_pyuvm.py    UVM-style env: agent, driver, monitor, scoreboard, coverage
formal/         sa_top.sby          SymbiYosys prove + cover
synth/          run_synth.sh, sta.tcl   Yosys synthesis sweep + OpenSTA timing
analysis/       perf_model.py, summarize_synth.py, merge_coverage.py, bug_injection.py
docs/           bug_injection.md    which test catches which injected RTL bug
setup/          install_tools.sh, check_tools.sh
```

## Quick start (WSL2 Ubuntu)

```bash
bash setup/install_tools.sh          # one time
source ~/.bashrc
make check                           # confirm the toolchain
make model                           # cycle-model self-test (numpy only)
make test                            # cocotb regression, N=4
make sweep                           # N = 2, 4, 8
make uvm                             # pyuvm regression
make cov                             # pyuvm at N = 2, 4, 8 + merged coverage closure check
make bugs                            # bug-injection check of the testbenches
make formal                          # SymbiYosys
make synth                           # Yosys (+ OpenSTA if $LIB is set)
make perf                            # performance model + plots in results/
```

## Verification

| Layer | What it checks |
|---|---|
| cocotb (7 tests) | identity, K=1, extreme values, 50 back-to-back jobs with the exact latency `K + DRAIN + N`, random bubbles + back-pressure, reset mid-job |
| cycle-accurate lockstep | `test_cycle_accurate_vs_model` compares `tready`, `tvalid`, `tlast`, `last_job_cycles` and `tdata` with `model/sa_cycle_model.py` on **every clock cycle**, with garbage on `tdata`/`tlast` while `tvalid` is low |
| pyuvm (3 tests) | agent / driver / monitor / scoreboard against a NumPy golden model: random + corner, random stalls, and directed stall patterns (valid toggling every cycle, long gaps, `tready` held low 8 / 30 / 50 cycles mid-output) |
| functional coverage | 27 bins (K, K vs N, operand extremes, sign mix, input bubbles per job, back-pressure cycles per job, stall patterns), merged over the tests by `make cov`; closure is checked per N |
| bug injection | 11 one-line RTL mutations, all caught - see [docs/bug_injection.md](docs/bug_injection.md) |

All of it passes for N = 2, 4, 8. Two examples of why the layers are needed: a DRAIN that is one
cycle too long is functionally correct and only a latency check or the lockstep test sees it;
`tready` high during DRAIN is invisible to the cocotb tests (they send the next job only after
the previous one has finished) and is caught by the lockstep test and by the pyuvm driver, which
overlaps jobs.

## Results

| N | Tests | Cells | Area (µm², sky130) | Fmax (MHz) | Peak GOPS @ Fmax |
|---|---|---|---|---|---|
| 2 | 7 cocotb + 3 pyuvm, all pass | _todo_ | _todo_ | _todo_ | _todo_ |
| 4 | 7 cocotb + 3 pyuvm, all pass | _todo_ | _todo_ | _todo_ | _todo_ |
| 8 | 7 cocotb + 3 pyuvm, all pass | _todo_ | _todo_ | _todo_ | _todo_ |

_Fill this table in week 3–4 (see PATHWAY.md)._
