# Top-level convenience targets.  Run from the project root inside WSL.
#   make check      -> verify the toolchain is installed
#   make model      -> Python cycle-model self-test (needs only numpy)
#   make test       -> cocotb regression (Icarus, N=4)    make test N=8
#   make sweep      -> cocotb regression for N = 2, 4, 8
#   make uvm        -> pyuvm UVM-style regression
#   make cov        -> pyuvm regression for N = 2, 4, 8 + merged functional-coverage closure
#   make bugs       -> bug-injection check of the testbenches (writes docs/bug_injection.md)
#   make formal     -> SymbiYosys proof + cover
#   make synth      -> Yosys synthesis sweep (+ OpenSTA if LIB is set)   PIPE=0|1 CLK_PERIOD=<ns>
#   make fmax       -> synthesis + STA for PIPE=0 and PIPE=1, then the before/after table + results/synth_area_fmax.png
#   make perf       -> analytical model, plots, and model-vs-RTL validation
#   make all        -> everything above

N ?= 4
# The OSS CAD Suite's Python 3.11: ~/sa-pylibs (numpy, cocotb 1.9.2, ...) is built for it,
# not for Ubuntu's python3 (3.10).  See CLAUDE.md "Environment".
PY ?= tabbypy3
# Clock for `make perf` (MHz): measured Fmax of the 8x8 PIPE=1 design (see README).
FREQ ?= 187.6
.PHONY: check model test sweep uvm cov bugs formal synth fmax perf all clean

check:
	@bash setup/check_tools.sh

model:
	$(PY) model/sa_cycle_model.py

test:
	$(MAKE) -C tb/cocotb N=$(N)

sweep:
	$(MAKE) -C tb/cocotb sweep

uvm:
	$(MAKE) -C tb/pyuvm N=$(N)

cov:
	rm -f results/cov_*.json
	$(MAKE) -C tb/pyuvm sweep
	$(PY) analysis/merge_coverage.py

bugs:
	$(PY) analysis/bug_injection.py

formal:
	cd formal && sby -f sa_top.sby

synth:
	bash synth/run_synth.sh
	$(PY) analysis/summarize_synth.py

fmax:
	bash synth/sweep_fmax.sh
	$(PY) analysis/summarize_synth.py
	$(PY) analysis/plot_synth.py

perf:
	$(PY) analysis/perf_model.py --freq $(FREQ)
	$(PY) analysis/perf_model.py --validate

all: model sweep uvm formal synth perf

clean:
	$(MAKE) -C tb/cocotb clean
	$(MAKE) -C tb/pyuvm clean
	rm -rf formal/sa_top_* synth/reports
