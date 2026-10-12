#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# area_breakdown.sh - Area per module (hierarchy kept, NOT flattened), sky130.
#
#   N=8 PIPE=1 ./area_breakdown.sh        # writes synth/reports/breakdown_N8_P1.txt
#
# Answers "what dominates the area?": the `stat -top` report gives every module's own
# area and the hierarchy total, plus the flop / combinational split from the cell list.
# Uses the same library, cell exclusions and timing-driven ABC script as run_synth.sh.
# -----------------------------------------------------------------------------
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
N="${N:-4}"
PIPE="${PIPE:-1}"
OUT="$HERE/reports"
mkdir -p "$OUT"
LIB="${LIB:-$HOME/tools/mm/env/share/pdk/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib}"
SRCS="$HERE/../rtl/pe.sv $HERE/../rtl/skew_buffer.sv $HERE/../rtl/systolic_array.sv $HERE/../rtl/sa_top.sv"

yosys -q -l "$OUT/breakdown_N${N}_P${PIPE}.log" -p "
  read_verilog -sv $SRCS
  chparam -set N $N -set PIPE $PIPE sa_top
  synth -top sa_top
  dfflibmap -liberty $LIB -dont_use sky130_fd_sc_hd__lpflow_*
  abc -script $HERE/abc_speed.script -liberty $LIB -constr $HERE/abc.constr -D 5000 -dont_use sky130_fd_sc_hd__lpflow_*
  opt_clean -purge
  tee -o $OUT/breakdown_N${N}_P${PIPE}.txt stat -liberty $LIB -top sa_top
"
echo "Report: $OUT/breakdown_N${N}_P${PIPE}.txt"
