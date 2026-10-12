# cocotb Makefile for sa_top
#   make                     -> Icarus Verilog, N=4
#   make N=8                 -> 8x8 array
#   make PIPE=0              -> PE without the product register (baseline)
#   make TOP=quant_relu MODULE=test_quant      -> requantization stage tests (QSTAGES=1..3)
#   make TOP=sa_layer   MODULE=test_sa_layer   -> GEMM + requantization tests
#   make TOP=sa_gemm    MODULE=test_sa_gemm    -> tiled GEMM with on-chip buffers
#                       (buffer capacity: MAXK=64 MAXMB=4 MAXNB=4)
#   make SIM=verilator       -> Verilator (needs Verilator >= 5.006)
#   make WAVES=1             -> dump waveforms (open with GTKWave / Surfer / WaveTrace)

SIM          ?= icarus
TOPLEVEL_LANG = verilog
N            ?= 4
PIPE         ?= 1
QSTAGES      ?= 1
MAXK         ?= 64
MAXMB        ?= 4
MAXNB        ?= 4

PROJ_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
RTL       := $(PROJ_ROOT)/rtl

VERILOG_SOURCES = $(RTL)/pe.sv \
                  $(RTL)/skew_buffer.sv \
                  $(RTL)/systolic_array.sv \
                  $(RTL)/sa_top.sv \
                  $(RTL)/quant_relu.sv \
                  $(RTL)/sa_layer.sv \
                  $(RTL)/sram_sp.sv \
                  $(RTL)/sa_gemm.sv

TOP      ?= sa_top
TOPLEVEL  = $(TOP)
MODULE   ?= test_sa_top

# pass the array size to the RTL and to Python
ifeq ($(SIM),icarus)
    COMPILE_ARGS += -g2012 -P$(TOPLEVEL).N=$(N)
    ifeq ($(TOPLEVEL),quant_relu)
        COMPILE_ARGS += -P$(TOPLEVEL).STAGES=$(QSTAGES)
    endif
    ifeq ($(TOPLEVEL),sa_layer)
        COMPILE_ARGS += -P$(TOPLEVEL).QSTAGES=$(QSTAGES)
    endif
    ifneq ($(TOPLEVEL),quant_relu)
        COMPILE_ARGS += -P$(TOPLEVEL).PIPE=$(PIPE)
    endif
    ifeq ($(TOPLEVEL),sa_gemm)
        COMPILE_ARGS += -P$(TOPLEVEL).MAXK=$(MAXK) -P$(TOPLEVEL).MAXMB=$(MAXMB) -P$(TOPLEVEL).MAXNB=$(MAXNB)
    endif
else ifeq ($(SIM),verilator)
    EXTRA_ARGS   += -GN=$(N) -Wno-fatal
    ifeq ($(TOPLEVEL),quant_relu)
        EXTRA_ARGS += -GSTAGES=$(QSTAGES)
    endif
    ifeq ($(TOPLEVEL),sa_layer)
        EXTRA_ARGS += -GQSTAGES=$(QSTAGES)
    endif
    ifneq ($(TOPLEVEL),quant_relu)
        EXTRA_ARGS += -GPIPE=$(PIPE)
    endif
    ifeq ($(TOPLEVEL),sa_gemm)
        EXTRA_ARGS += -GMAXK=$(MAXK) -GMAXMB=$(MAXMB) -GMAXNB=$(MAXNB)
    endif
    ifeq ($(WAVES),1)
        EXTRA_ARGS += --trace --trace-structs
    endif
endif

export SA_N := $(N)
export SA_PIPE := $(PIPE)
export SA_QSTAGES := $(QSTAGES)
export SA_MAXK := $(MAXK)
export SA_MAXMB := $(MAXMB)
export SA_MAXNB := $(MAXNB)
export PYTHONPATH := $(PROJ_ROOT)/tb/common:$(PYTHONPATH)

# separate build dir per configuration (top, N, PIPE, QSTAGES, buffer sizes): make does not
# track -P/-G parameter changes, so a shared directory would silently reuse a stale build
SIM_BUILD = sim_build_$(TOPLEVEL)_N$(N)_P$(PIPE)_Q$(QSTAGES)_K$(MAXK)_$(MAXMB)x$(MAXNB)

include $(shell cocotb-config --makefiles)/Makefile.sim

.PHONY: sweep
sweep:                        ## run the test-suite for N = 2, 4, 8
	$(MAKE) N=2
	$(MAKE) N=4
	$(MAKE) N=8

clean::
	rm -rf sim_build_N* results.xml *.vcd *.fst __pycache__
