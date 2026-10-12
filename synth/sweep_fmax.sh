#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# sweep_fmax.sh - Fmax before/after PE pipelining.
#
# Runs run_synth.sh for every combination of PIPE and clock-period target and keeps each
# run in synth/reports/pipe<P>_t<period>/. The mapper (ABC) is timing-driven toward the
# period target, so Fmax is taken as the best result over several targets.
#
#   ./sweep_fmax.sh                              # PIPE 0 1, period 5, N = 2 4 8
#   PIPES="0 1" PERIODS="3 5" SIZES="4" ./sweep_fmax.sh
#   python3 ../analysis/summarize_synth.py       # tables
# -----------------------------------------------------------------------------
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPES="${PIPES:-0 1}"
PERIODS="${PERIODS:-5}"
SIZES="${SIZES:-2 4 8}"

for P in $PIPES; do
  for T in $PERIODS; do
    echo "##### PIPE=$P  clock target ${T} ns #####"
    PIPE="$P" CLK_PERIOD="$T" SIZES="$SIZES" REPORT_DIR="$HERE/reports/pipe${P}_t${T}" \
      bash "$HERE/run_synth.sh" | grep -E "^===|wns_|rror" || true
  done
done
