# CLAUDE.md - context for Claude Code

## What this project is
A portfolio project for NVIDIA PS-II hardware roles (GPU ASIC Design/Verification,
GPU Architecture). An N×N output-stationary INT8 systolic-array GEMM engine with an
AXI4-Stream interface, plus verification, formal, synthesis, and performance analysis.
The owner is learning: **explain the reasoning behind every change** so they can defend
it in an interview. Prefer small, reviewable diffs over large rewrites.

## Environment
- Windows + **WSL2 Ubuntu**, editing in VS Code (Remote-WSL).
- Tools from **OSS CAD Suite** (`~/tools/oss-cad-suite/bin`): iverilog, verilator,
  yosys, sby, gtkwave. Optional: OpenSTA (`sta`).
- Python packages in `~/sa-pylibs` (installed with the OSS CAD Suite's own `tabbypy3`,
  put on `PYTHONPATH`/`PATH` by `~/.bashrc`): **cocotb 1.9.2** and **pyuvm 3.0.0**.
  No venv: the suite's `vvp` forces `PYTHONHOME` to the suite's Python 3.11, and the suite
  also bundles cocotb 2.x, which `~/sa-pylibs` shadows.
  Use cocotb 1.9 APIs (`units="ns"`, `.value.signed_integer`, `Task.kill()`),
  NOT cocotb 2.x APIs, unless we deliberately upgrade.
- `$LIB` = path to `sky130_fd_sc_hd__tt_025C_1v80.lib` (set by setup script).

## Commands (run from repo root)
| Task | Command |
|---|---|
| toolchain check | `make check` |
| cycle model self-test | `make model` |
| cocotb regression | `make test` / `make test N=8` / `make sweep` |
| waveforms | `make -C tb/cocotb WAVES=1` |
| Verilator instead of Icarus | `make -C tb/cocotb SIM=verilator` |
| pyuvm regression | `make uvm` |
| coverage closure (N=2,4,8) | `make cov` |
| bug-injection check | `make bugs` (writes docs/bug_injection.md) |
| formal | `make formal` |
| synthesis (+timing) | `make synth` (`PIPE=0\|1`, `CLK_PERIOD=<ns>`) |
| Fmax before/after PE pipelining | `make fmax` then `python3 analysis/summarize_synth.py` |
| performance model | `make perf` |

## Architecture rules (keep these invariants)
1. Data flow: A enters left and moves right; B enters top and moves down; C stays in PEs.
2. Skew: lane *i* is delayed exactly *i* **enabled** cycles (`skew_buffer.sv`).
3. **One global `en`** gates every register (skew + PEs). Stalls must freeze everything.
4. `clr` is synchronous and only asserted in the `CLEAR` state.
5. Latency (no stalls) = **K + DRAIN + N** cycles, DRAIN = (2N-2 for N>1, 1 for N=1) + `PIPE`.
   `PIPE=1` (default) registers the product inside every PE (shorter critical path, +1 cycle),
   `PIPE=0` is the unpipelined baseline, so K+3N-1 / K+3N-2 for N>=2. Checked by
   `test_random_back_to_back`, `test_cycle_accurate_vs_model` and `perf_model.py --validate`.
   If you change the pipeline, update `model/sa_cycle_model.py`,
   `analysis/perf_model.py:tile_cycles`, `tb/common/sa_utils.py:drain_cycles`, the tests and
   the README. Run the regressions for both `PIPE=1` and `PIPE=0` (`make sweep PIPE=0`).
6. Ports use **flat packed vectors** (no unpacked-array ports, no interfaces) so the code
   works in Icarus, Verilator, and Yosys. No `typedef enum`; FSM states are localparams.
7. Formal properties live in `rtl/sa_top.sv` under `` `ifdef FORMAL `` using immediate
   assertions + `$past` (open-source Yosys does not support concurrent SVA).
8. Synthesis/STA: sky130 liberty + OpenSTA (`sta`) live in the micromamba env
   `~/tools/mm/env`; `synth/run_synth.sh` finds them itself. Do not put that env's `bin/` on
   `PATH` (its Python and tools would shadow the OSS CAD Suite's).

## Verification rules
- The golden model is `tb/common/sa_utils.py:golden()` (NumPy, wraps to 32 bits).
- `model/sa_cycle_model.py` is the cycle-level reference; keep it in sync with the RTL.
- Every new feature needs: a cocotb test, a pyuvm sequence or coverage bin, and (if it is
  a protocol property) a formal assertion.
- Never weaken or delete a failing check to make a test pass. Find the root cause and
  explain it.

## Coding style
- SystemVerilog: `always_ff` for flops (async active-low reset `rst_n`), `always_comb`
  for logic, `logic` everywhere, named generate blocks (`g_*`), parameters in UPPER_CASE.
- Python: type hints, small functions, no global state beyond `N`.

## Current milestone
See the checklist in `PATHWAY.md`. Update the checkboxes there as work completes and
fill in the results table in `README.md`.
