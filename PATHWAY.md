# PATHWAY - Systolic-Array GEMM Accelerator (4 weeks + 2 stretch weeks)

**Goal:** a finished, defensible GPU ASIC / GPU Architecture portfolio project:
RTL → verification (cocotb + pyuvm + formal) → synthesis & timing (sky130) →
performance analysis, all on GitHub with numbers in the README.

**Time:** ~10–12 h/week. Each week ends with a **Definition of Done (DoD)**.
Tick the boxes as you go; Claude Code reads this file (see `CLAUDE.md`).

> **Important:** the starter code was written and its timing was checked against a
> cycle-accurate Python model (`make model` passes), but it had **not yet been run in a
> real HDL simulator** when this folder was handed over. Week 0's job is to run
> it, and fix any tool-specific issues (with Claude Code's help). Expect a few small fixes.

---

## Week 0 - Setup & first green run (1–2 evenings)

### 0.1 Install WSL2 + Ubuntu (Windows PowerShell as admin)
```powershell
wsl --install -d Ubuntu-22.04
```
Restart, open **Ubuntu**, create a user.

### 0.2 Put the project inside WSL (faster than /mnt/c)
```bash
mkdir -p ~/projects
cp -r "/mnt/c/Users/<you>/Documents/systolic-array-accelerator" ~/projects/
cd ~/projects/systolic-array-accelerator
git init && git add . && git commit -m "Starter: systolic array GEMM accelerator"
```

### 0.3 Install the toolchain (one command, ~10 min)
The script first runs `setup/place_templates.sh`, which copies the Makefiles and
`.vscode/` settings from `setup/templates/` into place (they ship there as templates).
```bash
bash setup/install_tools.sh
source ~/.bashrc
make check
```

### 0.4 VS Code
1. Install VS Code on Windows + the **WSL** extension.
2. In Ubuntu: `cd ~/projects/systolic-array-accelerator && code .`
3. Accept the recommended extensions (TerosHDL, Verilog-HDL, Python, WaveTrace, GitLens).
4. Optional (editor IntelliSense only): add `~/sa-pylibs` to `python.analysis.extraPaths`.
   There is no `.venv`; the simulator uses the OSS CAD Suite's Python (see `CLAUDE.md`).

### 0.5 Install Claude Code (in WSL)
Follow the official Claude Code install instructions, then run `claude` in the project
root. It will read `CLAUDE.md` automatically.

### 0.6 First runs
```bash
make model          # Python cycle model - should already pass
make test           # cocotb + Icarus, N=4
make uvm            # pyuvm
```
If anything fails, give Claude Code this prompt:
> *"Run `make test`. It fails with the error below. Find the root cause in the RTL or
> testbench, explain it to me, and propose the smallest fix. Do not weaken any check."*

**DoD Week 0**
- [x] `make check` shows iverilog, yosys, cocotb, pyuvm installed
- [x] `make model` passes
- [x] `make test` passes (6 cocotb tests, N=4)
- [x] `make uvm` passes (2 pyuvm tests)
- [x] Project pushed to a **public GitHub repo** (verify it is public and shows files)

---

## Week 1 - Understand and own the RTL

You must be able to explain every line. Read in this order:
`pe.sv → skew_buffer.sv → systolic_array.sv → sa_top.sv`.

### Tasks
- [ ] Draw the 4×4 array on paper and trace one 2×2 example cycle by cycle.
      Check your trace with `make -C tb/cocotb WAVES=1` and open the waveform
      (WaveTrace in VS Code, or `gtkwave`).
- [x] Work out the **minimum drain length** on paper. The last beat needs N−1 skew
      cycles + N−1 hops to reach PE(N−1,N−1), so the minimum is **2(N−1)**; the starter
      uses 2N (2 cycles of margin). Confirm in the cycle model (`m.drain_cyc = 2*n - 2`),
      then change `DRAIN_CYC` in `sa_top.sv` to `(N > 1) ? 2*N-2 : 1` (N=1 still needs one cycle). Latency becomes **K + 3N − 2**, so
      update the latency test, `tile_cycles()` in `analysis/perf_model.py`, `sa_cycle_model.py`,
      README and CLAUDE.md. Good first "optimisation + keep everything consistent" exercise.
- [ ] Explain why stalls are safe (single global `en`).
- [x] Run the size sweep: `make sweep` (N = 2, 4, 8).
- [x] Lint with Verilator: `verilator --lint-only -Wall rtl/*.sv --top-module sa_top`
      and fix or justify each warning.

### Concepts to learn (interview-critical)
- Output-stationary vs weight-stationary vs input-stationary dataflows (read the
  Eyeriss paper intro and the Google TPU v1 paper, section 2).
- Why systolic arrays: data reuse, short wires, regular layout.
- AXI4-Stream valid/ready handshake rules (data must be stable while valid && !ready).

**Claude Code prompts**
> *"Explain the timing of sa_top for N=2, K=3 cycle by cycle in a table: state, en,
> skew outputs, each PE's a_in/b_in/acc."*

**DoD Week 1:** you can whiteboard the design and its timing without notes.

---

## Week 2 - Verification depth

### Tasks
- [x] Add **functional coverage** that matters (extend `SaCoverage`):
      K bins, stall bins (# input bubbles per job), back-pressure bins, sign mix.
- [x] Add a **stall-pattern sequence**: valid toggling every cycle, long gaps, ready held
      low for 50 cycles mid-output.
- [x] Add a **cycle-accurate check**: drive the same stimulus into
      `model/sa_cycle_model.py` and compare `s_axis_tready` / `m_axis_tvalid` cycle by
      cycle (not just final results).
- [x] Bug-injection exercise: change one thing in the RTL (e.g. skew lane 2 delay = 1,
      or `acc` width 16) and confirm the tests catch it. Note which test caught it.
- [ ] Optional: port the pyuvm bench to **SystemVerilog UVM** on
      [EDA Playground](https://www.edaplayground.com) (free account → Synopsys VCS /
      Cadence Xcelium). Even a small agent + scoreboard is a strong resume line.

**Claude Code prompts**
> *"Add a cycle-accurate comparison between the RTL and model/sa_cycle_model.py to the
> cocotb bench. Keep it in a separate test."*
> *"Add coverage bins for stall patterns to SaCoverage and print a coverage summary."*

**DoD Week 2:** regression green on N=2/4/8, coverage report with all bins hit, and a
short list of injected bugs and which test caught each.

---

## Week 3 - Formal, synthesis, timing

### 3.1 Formal (SymbiYosys)
```bash
make formal          # runs 'prove' (k-induction) and 'cover'
```
- [x] Get `prove` to PASS. If induction fails on an unreachable state, add the missing
      invariant as an `assert` (this is normal and a great interview story).
- [x] Add one property of your own, e.g. "`s_axis_tready` is never high during DRAIN",
      or "after the last output beat the FSM returns to CLEAR".
- [ ] Look at the `cover` trace (`formal/sa_top_cover/engine_0/trace0.vcd`).

### 3.2 Synthesis
```bash
make synth           # generic gates if $LIB is unset; sky130 area if set
```
- [x] Record cells and area for N = 2, 4, 8. Plot area vs N² - it should be ~linear.
- [x] Find what dominates area (multipliers? accumulators? skew regs?) using
      `stat` per module (remove `-flatten` in `run_synth.sh` for a hierarchical report).

### 3.3 Static timing (OpenSTA)
OpenSTA is not in OSS CAD Suite. **What worked here (no root, no Docker):** `setup/install_tools.sh`
creates a micromamba env `~/tools/mm/env` from the `litex-hub` channel with `openroad` (includes the
`sta` binary) and `open_pdks.sky130a` (the liberty files, ~1 GB download / ~9 GB on disk).
`synth/run_synth.sh` finds `sta` and `LIB` there by itself. Other options:
1. **Docker:** `docker run -it -v $PWD:/work openroad/orfs:latest bash` (contains `sta`),
   then run `bash synth/run_synth.sh` inside the container.
2. **Build from source:** <https://github.com/The-OpenROAD-Project/OpenSTA> (README build steps).

If the sky130 liberty download failed: `pip install volare && volare enable --pdk sky130 <version>`
(see the volare README), then point `LIB` at
`~/.volare/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib`.

```bash
CLK_PERIOD=5 make synth         # or: make fmax  (PIPE=0 vs PIPE=1, tables + plot)
```
- [x] Find **Fmax** and the **critical path** (expect: multiplier → adder → acc register).
- [x] **Optimise:** pipeline the PE (register the product before accumulating). This adds
      one cycle of latency → update DRAIN, the cycle model, `tile_cycles`, the latency
      test, and the README. Show Fmax before vs after. **This is your best interview story.**

**DoD Week 3:** formal proof passes, a table of area/Fmax for N = 2/4/8, and a
before/after result for the PE pipelining.

---

## Week 4 - Performance analysis & presentation

### Tasks
- [ ] `make perf` → table + `results/utilization_vs_size.png` + `results/roofline_n32.png`.
- [ ] `python3 analysis/perf_model.py --validate` after `make sweep` - model must match RTL.
- [ ] Use **your Fmax** from week 3: `python3 analysis/perf_model.py --freq <Fmax>`.
- [ ] Write the analysis in the README:
  - Utilization drops for small layers / big arrays (fill + drain overhead = 3N per tile).
  - The roofline shows the design is **interface-bandwidth bound** (2N bytes/cycle in);
    explain why weight-stationary or on-chip buffering would raise arithmetic intensity.
- [ ] Fill in the README results table, add the plots, and add a block diagram.
- [ ] Record a 2-minute demo video or GIF of the waveform (optional, but helps).

**DoD Week 4:** README tells the whole story with numbers. Repo is pinned on GitHub.

---

## Stretch (weeks 5–6) - pick 1 or 2
- [ ] **Weight-stationary variant** and a comparison table vs output-stationary
      (utilization, bandwidth, area). Directly relevant to GPU/TPU architecture interviews.
- [ ] **Integrate your AXI4-Stream quantization block** on the output: INT32 → ReLU → INT8.
      Turns this into a real "layer engine".
- [ ] **Tiling controller + SRAM buffers**: compute an M×K×Nc GEMM bigger than N×N by
      looping over tiles in hardware, and reuse B tiles from on-chip memory.
- [ ] **OpenLane → GDS** on sky130: full place & route, report area/WNS/power.
- [ ] **C++ performance model** (cycle-level, mirrors the Python model) - bridges to the
      "Mini SIMT GPU" project for the GPU Architecture role.

---

## Resume bullets (fill in the real numbers)
**Systolic-Array INT8 GEMM Accelerator** | SystemVerilog, cocotb, pyuvm, Yosys, OpenSTA, SymbiYosys
- Designed a parameterized **N×N output-stationary systolic array** with AXI4-Stream
  interfaces and stall-safe global enable, sustaining **<util>% PE utilization** on
  ResNet-18 layers.
- Verified with a **pyuvm environment** (driver, monitor, scoreboard, coverage) and
  **<n> constrained-random jobs** under random back-pressure; proved AXI handshake
  properties with **SymbiYosys k-induction**.
- Synthesized on **SkyWater 130 nm**: **<area> µm²** and **<Fmax> MHz** for 8×8; pipelining
  the PE raised Fmax by **<x>%**.
- Built a **cycle-accurate model** and **roofline analysis** matching RTL latency
  (K + 3N cycles) exactly; identified interface bandwidth as the bottleneck.

## Interview questions you should be ready for
1. Why output-stationary? What changes for weight-stationary?
2. Walk me through the skew. Why does lane *i* need *i* cycles?
3. How do you guarantee correctness under back-pressure?
4. What is your critical path and how did you fix it?
5. How did you know your verification was complete? (coverage, formal, bug injection)
6. Utilization for a 32×32 array on a 7×7 layer? Why so low?
7. How would this map onto a GPU tensor core?
8. How does accumulator width relate to K? (INT8×INT8 = 16 bits; K products need
   16 + log2(K) bits → 32 bits is safe up to K = 65 536.)
