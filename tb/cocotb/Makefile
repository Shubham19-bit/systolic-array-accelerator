# cocotb Makefile for sa_top
#   make                     -> Icarus Verilog, N=4
#   make N=8                 -> 8x8 array
#   make PIPE=0              -> PE without the product register (baseline)
#   make SIM=verilator       -> Verilator (needs Verilator >= 5.006)
#   make WAVES=1             -> dump waveforms (open with GTKWave / Surfer / WaveTrace)

SIM          ?= icarus
TOPLEVEL_LANG = verilog
N            ?= 4
PIPE         ?= 1

PROJ_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
RTL       := $(PROJ_ROOT)/rtl

VERILOG_SOURCES = $(RTL)/pe.sv \
                  $(RTL)/skew_buffer.sv \
                  $(RTL)/systolic_array.sv \
                  $(RTL)/sa_top.sv

TOPLEVEL = sa_top
MODULE   = test_sa_top

# pass the array size to the RTL and to Python
ifeq ($(SIM),icarus)
    COMPILE_ARGS += -g2012 -P$(TOPLEVEL).N=$(N) -P$(TOPLEVEL).PIPE=$(PIPE)
else ifeq ($(SIM),verilator)
    EXTRA_ARGS   += -GN=$(N) -GPIPE=$(PIPE) -Wno-fatal
    ifeq ($(WAVES),1)
        EXTRA_ARGS += --trace --trace-structs
    endif
endif

export SA_N := $(N)
export SA_PIPE := $(PIPE)
export PYTHONPATH := $(PROJ_ROOT)/tb/common:$(PYTHONPATH)

# separate build dir per configuration so N=4/N=8 and PIPE=0/1 builds don't collide
SIM_BUILD = sim_build_N$(N)_P$(PIPE)

include $(shell cocotb-config --makefiles)/Makefile.sim

.PHONY: sweep
sweep:                        ## run the test-suite for N = 2, 4, 8
	$(MAKE) N=2
	$(MAKE) N=4
	$(MAKE) N=8

clean::
	rm -rf sim_build_N* results.xml *.vcd *.fst __pycache__
