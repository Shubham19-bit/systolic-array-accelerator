# Top-level convenience targets.  Run from the project root inside WSL.
#   make check      -> verify the toolchain is installed
#   make model      -> Python cycle-model self-test (needs only numpy)
#   make test       -> cocotb regression (Icarus, N=4)    make test N=8
#   make sweep      -> cocotb regression for N = 2, 4, 8
#   make uvm        -> pyuvm UVM-style regression
#   make formal     -> SymbiYosys proof + cover
#   make synth      -> Yosys synthesis sweep (+ OpenSTA if LIB is set)
#   make perf       -> analytical model, plots, and model-vs-RTL validation
#   make all        -> everything above

N ?= 4
# The OSS CAD Suite's Python 3.11: ~/sa-pylibs (numpy, cocotb 1.9.2, ...) is built for it,
# not for Ubuntu's python3 (3.10).  See CLAUDE.md "Environment".
PY ?= tabbypy3
.PHONY: check model test sweep uvm formal synth perf all clean

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

formal:
	cd formal && sby -f sa_top.sby

synth:
	bash synth/run_synth.sh
	$(PY) analysis/summarize_synth.py

perf:
	$(PY) analysis/perf_model.py
	$(PY) analysis/perf_model.py --validate

all: model sweep uvm formal synth perf

clean:
	$(MAKE) -C tb/cocotb clean
	$(MAKE) -C tb/pyuvm clean
	rm -rf formal/sa_top_* synth/reports
