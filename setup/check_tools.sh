#!/usr/bin/env bash
# check_tools.sh - report which tools are installed and their versions.
ok=0; miss=0
check() {  # name, command, version-cmd, required(yes/no), purpose
  if command -v "$2" >/dev/null 2>&1; then
    printf "  [ok]      %-14s %s\n" "$1" "$($3 2>&1 | head -1)"
    ok=$((ok+1))
  else
    if [[ "$4" == "yes" ]]; then
      printf "  [MISSING] %-14s needed for: %s\n" "$1" "$5"
    else
      printf "  [optional]%-14s needed for: %s\n" "$1" "$5"
    fi
    miss=$((miss+1))
  fi
}
echo "Toolchain check:"
check "python3"    python3   "python3 --version"   yes "everything"
check "make"       make      "make --version"      yes "everything"
check "iverilog"   iverilog  "iverilog -V"         yes "simulation (default)"
check "verilator"  verilator "verilator --version" no  "fast simulation, linting"
check "yosys"      yosys     "yosys -V"            yes "synthesis"
check "sby"        sby       "sby --help"          no  "formal verification"
check "gtkwave"    gtkwave   "gtkwave --version"   no  "waveform viewing (or use VS Code WaveTrace)"
check "sta"        sta       "sta -version"        no  "static timing analysis (week 3)"

echo "Python packages:"
for p in cocotb pyuvm numpy matplotlib; do
  v=$(python3 -c "import $p; print(getattr($p,'__version__','?'))" 2>/dev/null) \
    && printf "  [ok]      %-14s %s\n" "$p" "$v" \
    || printf "  [MISSING] %-14s run: pip install -r requirements.txt\n" "$p"
done

if [[ -n "${LIB:-}" && -f "$LIB" ]]; then
  echo "Liberty:   LIB=$LIB"
else
  echo "Liberty:   LIB not set (generic synthesis only; see PATHWAY.md week 3)"
fi
