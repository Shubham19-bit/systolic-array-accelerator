"""
summarize_synth.py - Collect Yosys / OpenSTA reports into one table.

Reads synth/reports/*.rpt produced by synth/run_synth.sh and prints:
  N | cells | area (um^2, if a liberty was used) | WNS (ns) | est. Fmax (MHz)
"""
from __future__ import annotations

import glob
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(HERE, "..", "synth", "reports")


def grab(pattern, text, cast=float):
    m = re.search(pattern, text)
    return cast(m.group(1)) if m else None


def main():
    sizes = set()
    for f in glob.glob(os.path.join(REP, "*_N*.rpt")):
        m = re.search(r"_N(\d+)\.rpt$", f)
        if m:
            sizes.add(int(m.group(1)))
    if not sizes:
        print("No reports found - run synth/run_synth.sh first.")
        return

    period = float(os.environ.get("CLK_PERIOD", "10"))
    print(f"{'N':>4} {'cells':>10} {'area_um2':>12} {'WNS_ns':>9} {'Fmax_MHz':>10}")
    for n in sorted(sizes):
        cells = area = wns = None
        for name in (f"area_N{n}.rpt", f"stat_generic_N{n}.rpt"):
            p = os.path.join(REP, name)
            if os.path.exists(p):
                t = open(p).read()
                cells = grab(r"Number of cells:\s+(\d+)", t, int) or \
                    grab(r"(\d+)\s+cells", t, int)
                area = grab(r"Chip area for (?:top )?module.*?:\s+([\d.]+)", t)
                break
        p = os.path.join(REP, f"timing_N{n}.rpt")
        if os.path.exists(p):
            wns = grab(r"wns\s+(-?[\d.]+)", open(p).read())
        fmax = 1000.0 / (period - wns) if wns is not None and period - wns > 0 else None
        print(f"{n:>4} {cells if cells is not None else '-':>10} "
              f"{f'{area:.0f}' if area else '-':>12} "
              f"{f'{wns:.3f}' if wns is not None else '-':>9} "
              f"{f'{fmax:.1f}' if fmax else '-':>10}")


if __name__ == "__main__":
    main()
