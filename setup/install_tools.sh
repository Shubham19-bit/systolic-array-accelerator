#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# install_tools.sh - One-shot toolchain install for WSL2 Ubuntu (22.04 / 24.04)
#
#   bash setup/install_tools.sh
#
# Installs:
#   * OSS CAD Suite  -> Icarus Verilog, Verilator, Yosys, SymbiYosys, GTKWave, ...
#                       (into ~/tools/oss-cad-suite, added to ~/.bashrc)
#   * Python packages -> cocotb, pyuvm, numpy, matplotlib (into ~/sa-pylibs, suite Python)
#   * sky130 HD standard-cell liberty file (for real area/timing, week 3)
# OpenSTA is optional and covered in PATHWAY.md (week 3).
# -----------------------------------------------------------------------------
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="$HOME/tools"
mkdir -p "$TOOLS"

echo ">>> [0/4] placing Makefiles and VS Code settings"
bash "$ROOT/setup/place_templates.sh"

echo ">>> [1/4] apt packages (python, make, git, curl)"
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv make git curl tar

echo ">>> [2/4] OSS CAD Suite (latest nightly build from YosysHQ)"
if [[ ! -d "$TOOLS/oss-cad-suite" ]]; then
  URL=$(curl -s https://api.github.com/repos/YosysHQ/oss-cad-suite-build/releases/latest \
        | grep -o '"browser_download_url": *"[^"]*linux-x64[^"]*\.tgz"' \
        | head -1 | sed 's/.*"\(https[^"]*\)"/\1/')
  if [[ -z "$URL" ]]; then
    echo "Could not find the download URL automatically."
    echo "Download 'oss-cad-suite-linux-x64-*.tgz' from"
    echo "  https://github.com/YosysHQ/oss-cad-suite-build/releases/latest"
    echo "and extract it to $TOOLS, then re-run this script."
    exit 1
  fi
  echo "Downloading $URL"
  curl -L "$URL" -o "$TOOLS/oss-cad-suite.tgz"
  tar -xzf "$TOOLS/oss-cad-suite.tgz" -C "$TOOLS"
  rm "$TOOLS/oss-cad-suite.tgz"
fi
if ! grep -q "oss-cad-suite/bin" "$HOME/.bashrc"; then
  echo 'export PATH="$HOME/tools/oss-cad-suite/bin:$PATH"' >> "$HOME/.bashrc"
fi
export PATH="$TOOLS/oss-cad-suite/bin:$PATH"

echo ">>> [3/4] Python virtual environment + packages"
cd "$ROOT"
# No venv: oss-cad-suite/bin/vvp is a wrapper that forces PYTHONHOME to the suite, so the
# simulator embeds the SUITE's Python 3.11 (a venv on Ubuntu's 3.10 dies with
# "No module named 'encodings'", and the suite's Python cannot create a venv).
# The suite bundles cocotb 2.x but this project uses 1.9 APIs, so install the pinned
# packages into a private folder and let PYTHONPATH shadow the bundled cocotb.
PYLIBS="$HOME/sa-pylibs"
"$TOOLS/oss-cad-suite/bin/tabbypy3" -m pip install --target "$PYLIBS" -r requirements.txt
if ! grep -q "sa-pylibs" "$HOME/.bashrc"; then
  {
    echo 'export PYTHONPATH="$HOME/sa-pylibs:$PYTHONPATH"'
    echo 'export PATH="$HOME/sa-pylibs/bin:$PATH"'
  } >> "$HOME/.bashrc"
fi
export PYTHONPATH="$PYLIBS:${PYTHONPATH:-}"
export PATH="$PYLIBS/bin:$PATH"

echo ">>> [4/4] sky130 HD liberty (typical corner)"
mkdir -p "$TOOLS/sky130"
LIBF="$TOOLS/sky130/sky130_fd_sc_hd__tt_025C_1v80.lib"
if [[ ! -f "$LIBF" ]]; then
  curl -L -f -o "$LIBF" \
    https://raw.githubusercontent.com/google/skywater-pdk-libs-sky130_fd_sc_hd/main/timing/sky130_fd_sc_hd__tt_025C_1v80.lib \
    || echo "WARN: liberty download failed - see PATHWAY.md week 3 for the 'volare' alternative."
fi
if [[ -f "$LIBF" ]] && ! grep -q "sky130_fd_sc_hd__tt_025C_1v80.lib" "$HOME/.bashrc"; then
  echo "export LIB=$LIBF" >> "$HOME/.bashrc"
fi

echo
echo "Done. Open a NEW terminal (or run 'source ~/.bashrc'), then:"
echo "  cd $ROOT && make check"
