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
#   * sky130 PDK + OpenSTA/OpenROAD (micromamba env in ~/tools/mm/env; real area/timing,
#                       week 3). Optional: SKIP_PDK=1 bash setup/install_tools.sh
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

echo ">>> [4/4] sky130 PDK (liberty) + OpenSTA + OpenROAD via micromamba (no root needed)"
# The old raw.githubusercontent.com liberty URL returns 404, and OpenSTA is not in the OSS
# CAD Suite. The litex-hub conda channel ships both `open_pdks.sky130a` (all sky130 libs)
# and `openroad` (which includes the `sta` binary). ~1 GB download, ~9 GB on disk.
# Skip with SKIP_PDK=1. synth/run_synth.sh finds LIB and sta in ~/tools/mm/env by itself.
MM="$TOOLS/mm"
if [[ "${SKIP_PDK:-0}" != "1" && ! -x "$MM/env/bin/sta" ]]; then
  mkdir -p "$MM"
  curl -L -f https://micro.mamba.pm/api/micromamba/linux-64/latest | tar -xj -C "$MM" bin/micromamba
  MAMBA_ROOT_PREFIX="$MM/root" "$MM/bin/micromamba" create -y -p "$MM/env" \
    -c litex-hub -c conda-forge openroad open_pdks.sky130a \
    || echo "WARN: PDK/OpenSTA install failed - synthesis still works without it (generic gates)."
fi

echo
echo "Done. Open a NEW terminal (or run 'source ~/.bashrc'), then:"
echo "  cd $ROOT && make check"
