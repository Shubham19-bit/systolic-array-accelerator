#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# run_synth.sh - Yosys synthesis sweep (+ optional OpenSTA timing) for sa_top
#
# Generic (no library needed):
#   ./run_synth.sh                       # N = 2 4 8, generic gates
#
# With the SkyWater 130nm standard-cell library (real area + timing):
#   export LIB=/path/to/sky130_fd_sc_hd__tt_025C_1v80.lib
#   ./run_synth.sh                       # area in um^2 + OpenSTA timing report
#   CLK_PERIOD=8 ./run_synth.sh          # target clock period in ns (default 10)
#   SIZES="4 16" ./run_synth.sh          # choose array sizes
#
# Reports are written to synth/reports/.
# -----------------------------------------------------------------------------
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RTL="$HERE/../rtl"
OUT="$HERE/reports"
SIZES="${SIZES:-2 4 8}"
CLK_PERIOD="${CLK_PERIOD:-10}"
mkdir -p "$OUT"

SRCS="$RTL/pe.sv $RTL/skew_buffer.sv $RTL/systolic_array.sv $RTL/sa_top.sv"

for N in $SIZES; do
  echo "=== Synthesizing sa_top N=$N ==="
  if [[ -n "${LIB:-}" ]]; then
    yosys -q -l "$OUT/yosys_N${N}.log" -p "
      read_verilog -sv $SRCS
      chparam -set N $N sa_top
      synth -top sa_top -flatten
      dfflibmap -liberty $LIB
      abc -liberty $LIB
      opt_clean
      tee -o $OUT/area_N${N}.rpt stat -liberty $LIB
      write_verilog -noattr $OUT/sa_top_N${N}_netlist.v
    "
    if command -v sta >/dev/null 2>&1; then
      echo "--- OpenSTA timing (period ${CLK_PERIOD} ns) ---"
      LIB="$LIB" NETLIST="$OUT/sa_top_N${N}_netlist.v" CLK_PERIOD="$CLK_PERIOD" \
        sta -no_splash -exit "$HERE/sta.tcl" | tee "$OUT/timing_N${N}.rpt"
    else
      echo "OpenSTA ('sta') not found - skipping timing. See PATHWAY.md week 3."
    fi
  else
    yosys -q -l "$OUT/yosys_N${N}.log" -p "
      read_verilog -sv $SRCS
      chparam -set N $N sa_top
      synth -top sa_top -flatten
      tee -o $OUT/stat_generic_N${N}.rpt stat
    "
  fi
done

echo
echo "Done. Summarise with:  python3 $HERE/../analysis/summarize_synth.py"
