"""
summarize_synth.py - Collect Yosys / OpenSTA reports into tables.

Reads the *.rpt files written by synth/run_synth.sh (and synth/sweep_fmax.sh, which puts
each run in synth/reports/pipe<P>_t<period>/) and prints, per report directory and N:

  cells | area (um^2, needs a liberty) | reg2reg slack | core Fmax | Fmax incl. inputs

  core Fmax          = 1000 / (period - WNS_reg2reg)   flop -> flop: multiplier -> adder -> acc
  Fmax incl. inputs  = the same using the worst of reg2reg / in2reg / reg2out
                       (I/O delays are 0, so this is an ideal-I/O figure)

When the directories follow the pipe<P>_t<period> naming, a second table gives the best
(highest) Fmax over the clock-period targets for every (PIPE, N), which is the number to
quote: the mapper is timing-driven, so the result depends a little on the target.

Usage:  python analysis/summarize_synth.py [report_dir ...]
"""
from __future__ import annotations

import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REP = os.path.join(HERE, "..", "synth", "reports")


def grab(pattern, text, cast=float):
    m = re.search(pattern, text, re.M)
    return cast(m.group(1)) if m else None


def read(path):
    return open(path).read() if os.path.exists(path) else ""


def fmax(period, wns):
    return 1000.0 / (period - wns) if wns is not None and period - wns > 0 else None


def collect(d):
    """Rows (N, cells, area, period, wns dict) for one report directory."""
    sizes = sorted({int(m.group(1)) for f in glob.glob(os.path.join(d, "*_N*.rpt"))
                    if (m := re.search(r"_N(\d+)\.rpt$", f))})
    rows = []
    for n in sizes:
        area_t = read(os.path.join(d, f"area_N{n}.rpt")) or \
            read(os.path.join(d, f"stat_generic_N{n}.rpt"))
        # liberty `stat`: "  11544 9.64E+04 cells"; generic `stat`: "  4180 cells"
        cells = grab(r"^\s*(\d+)\s+[\d.]+E[+-]\d+\s+cells", area_t, int) or \
            grab(r"^\s*(\d+)\s+cells", area_t, int) or \
            grab(r"Number of cells:\s+(\d+)", area_t, int)
        area = grab(r"Chip area for (?:top )?module.*?:\s+([\d.]+)", area_t)
        t = read(os.path.join(d, f"timing_N{n}.rpt"))
        period = grab(r"^clk_period\s+([\d.]+)", t)
        wns = {g: grab(rf"^wns_{g}\s+(-?[\d.]+)", t) for g in ("reg2reg", "in2reg", "reg2out")}
        rows.append((n, cells, area, period, wns))
    return rows


def fmt(v, spec):
    return "-" if v is None else format(v, spec)


def main():
    dirs = sys.argv[1:] or [REP] + sorted(
        p for p in glob.glob(os.path.join(REP, "*")) if os.path.isdir(p))
    best = {}                                        # (pipe, N) -> row dict with best Fmax
    printed = False
    for d in dirs:
        rows = collect(d)
        if not rows:
            continue
        printed = True
        tag = os.path.basename(os.path.normpath(d))
        print(f"\n[{tag}]")
        print(f"{'N':>4} {'cells':>8} {'area_um2':>10} {'period':>7} {'reg2reg_ns':>11} "
              f"{'core_MHz':>9} {'incl_in_MHz':>12}")
        for n, cells, area, period, wns in rows:
            core = fmax(period, wns["reg2reg"]) if period else None
            worst = min((v for v in wns.values() if v is not None), default=None)
            allf = fmax(period, worst) if period else None
            print(f"{n:>4} {fmt(cells, 'd'):>8} {fmt(area, '.0f'):>10} {fmt(period, '.1f'):>7} "
                  f"{fmt(wns['reg2reg'], '.3f'):>11} {fmt(core, '.1f'):>9} {fmt(allf, '.1f'):>12}")
            m = re.match(r"pipe(\d+)_t", tag)
            if m and core:
                key = (int(m.group(1)), n)
                if key not in best or core > best[key]["core"]:
                    best[key] = dict(core=core, all=allf, cells=cells, area=area,
                                     period=period, tag=tag)
    if not printed:
        print("No reports found - run synth/run_synth.sh (or make synth) first.")
        return
    if best:
        print("\nBest over clock-period targets (the number to quote):")
        print(f"{'PIPE':>5} {'N':>4} {'cells':>8} {'area_um2':>10} {'core_MHz':>9} "
              f"{'incl_in_MHz':>12}  from")
        for (pipe, n), b in sorted(best.items()):
            print(f"{pipe:>5} {n:>4} {fmt(b['cells'], 'd'):>8} {fmt(b['area'], '.0f'):>10} "
                  f"{b['core']:>9.1f} {fmt(b['all'], '.1f'):>12}  {b['tag']}")


if __name__ == "__main__":
    main()
