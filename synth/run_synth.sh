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
#
# Other knobs:  PIPE=0|1 (PE product register, default 1)   REPORT_DIR=<dir>
#               ABC_AREA=1 (minimum-size mapping, for a pure area comparison)
# LIB and sta are auto-detected in ~/tools/mm/env (micromamba env with the sky130 PDK
# and OpenSTA, see PATHWAY.md week 3) when they are not already set / on PATH.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RTL="$HERE/../rtl"
OUT="${REPORT_DIR:-$HERE/reports}"
SIZES="${SIZES:-2 4 8}"
CLK_PERIOD="${CLK_PERIOD:-10}"
PIPE="${PIPE:-1}"
D_PS="$(awk "BEGIN{printf \"%d\", $CLK_PERIOD*1000}")"     # ABC delay target in ps
mkdir -p "$OUT"

MM_ENV="$HOME/tools/mm/env"
DEFAULT_LIB="$MM_ENV/share/pdk/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
if [[ -z "${LIB:-}" && -f "$DEFAULT_LIB" ]]; then LIB="$DEFAULT_LIB"; fi
STA="$(command -v sta || true)"
if [[ -z "$STA" && -x "$MM_ENV/bin/sta" ]]; then STA="$MM_ENV/bin/sta"; fi

SRCS="$RTL/pe.sv $RTL/skew_buffer.sv $RTL/systolic_array.sv $RTL/sa_top.sv"

# lpflow_* cells (low-power-flow variants) are excluded, as in the sky130 reference flows.
# Default: timing-driven mapping with fan-out buffering and sizing (synth/abc_speed.script).
# ABC_AREA=1: plain `abc -liberty` (minimum-size gates, no buffering) for a pure area number.
if [[ "${ABC_AREA:-0}" == "1" ]]; then
  ABC_CMD="abc -liberty ${LIB:-} -dont_use sky130_fd_sc_hd__lpflow_*"
else
  ABC_CMD="abc -script $HERE/abc_speed.script -liberty ${LIB:-} -constr $HERE/abc.constr -D $D_PS -dont_use sky130_fd_sc_hd__lpflow_*"
fi

for N in $SIZES; do
  echo "=== Synthesizing sa_top N=$N PIPE=$PIPE ==="
  if [[ -n "${LIB:-}" ]]; then
    yosys -q -l "$OUT/yosys_N${N}.log" -p "
      read_verilog -sv $SRCS
      chparam -set N $N -set PIPE $PIPE sa_top
      synth -top sa_top -flatten
      dfflibmap -liberty $LIB -dont_use sky130_fd_sc_hd__lpflow_*
      $ABC_CMD
      opt_clean -purge
      tee -o $OUT/area_N${N}.rpt stat -liberty $LIB
      write_verilog -noattr $OUT/sa_top_N${N}_netlist.v
    "
    # OpenSTA's Verilog reader does not accept the 'signed' keyword on wires
    sed -i 's/\bwire signed /wire /' "$OUT/sa_top_N${N}_netlist.v"
    if [[ -n "$STA" ]]; then
      echo "--- OpenSTA timing (period ${CLK_PERIOD} ns) ---"
      LIB="$LIB" NETLIST="$OUT/sa_top_N${N}_netlist.v" CLK_PERIOD="$CLK_PERIOD" \
        "$STA" -no_splash -exit "$HERE/sta.tcl" | tee "$OUT/timing_N${N}.rpt"
    else
      echo "OpenSTA ('sta') not found - skipping timing. See PATHWAY.md week 3."
    fi
  else
    yosys -q -l "$OUT/yosys_N${N}.log" -p "
      read_verilog -sv $SRCS
      chparam -set N $N -set PIPE $PIPE sa_top
      synth -top sa_top -flatten
      tee -o $OUT/stat_generic_N${N}.rpt stat
    "
  fi
done

echo
echo "Done. Summarise with:  python3 $HERE/../analysis/summarize_synth.py"
