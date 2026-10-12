#!/usr/bin/env bash
# place_templates.sh - copy the Makefiles and VS Code config into place.
# (They ship under setup/templates/ because they could not be written directly.)
# Safe to re-run: existing files are never overwritten.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
T="$ROOT/setup/templates"
place() { if [[ ! -f "$2" ]]; then mkdir -p "$(dirname "$2")"; cp "$1" "$2"; echo "  placed $2"; fi; }
place "$T/Makefile.root"          "$ROOT/Makefile"
place "$T/Makefile.cocotb"        "$ROOT/tb/cocotb/Makefile"
place "$T/Makefile.pyuvm"         "$ROOT/tb/pyuvm/Makefile"
place "$T/vscode_extensions.json" "$ROOT/.vscode/extensions.json"
place "$T/vscode_settings.json"   "$ROOT/.vscode/settings.json"
chmod +x "$ROOT"/setup/*.sh "$ROOT"/synth/*.sh
echo "Templates in place."
