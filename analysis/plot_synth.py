"""
plot_synth.py - Area vs N^2 and core Fmax for PIPE=0 / PIPE=1 -> results/synth_area_fmax.png

Reads the report directories synth/reports/pipe<P>_t<period>/ written by
synth/sweep_fmax.sh (run `make fmax` first). Two panels instead of a dual axis:
  left : sky130 area (um^2) against N^2 - a straight line means area grows with PE count
  right: core Fmax (flop -> flop, OpenSTA) against N
"""
from __future__ import annotations

import glob
import os
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from plot_style import CATEGORICAL, GRID, INK, MUTED, SURFACE  # noqa: E402
from summarize_synth import REP, collect, fmax  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")

SERIES = {0: ("PE not pipelined (PIPE=0)", CATEGORICAL[0]),
          1: ("PE with product register (PIPE=1)", CATEGORICAL[1])}


def best_rows() -> dict[int, dict[int, tuple[float, float]]]:
    """pipe -> N -> (area_um2, core_MHz), best Fmax over the available period targets."""
    out: dict[int, dict[int, tuple[float, float]]] = {0: {}, 1: {}}
    for d in glob.glob(os.path.join(REP, "pipe*_t*")):
        m = re.match(r"pipe(\d+)_t", os.path.basename(d))
        if not m:
            continue
        pipe = int(m.group(1))
        for n, _cells, area, period, wns in collect(d):
            core = fmax(period, wns["reg2reg"]) if period else None
            if area and core and core > out[pipe].get(n, (0, 0))[1]:
                out[pipe][n] = (area, core)
    return out


def style(ax):
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)


def main():
    data = best_rows()
    if not data[0] or not data[1]:
        print("Need reports for PIPE=0 and PIPE=1 - run `make fmax` first.")
        return
    sizes = sorted(set(data[0]) & set(data[1]))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4), facecolor=SURFACE)

    for pipe, (label, color) in SERIES.items():
        xs = [n * n for n in sizes]
        ys = [data[pipe][n][0] / 1000 for n in sizes]
        ax1.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=8,
                 markeredgecolor=SURFACE, markeredgewidth=2, label=label)
        for x, y in zip(xs, ys):
            ax1.annotate(f"{y:.0f}", (x, y), textcoords="offset points",
                         xytext=(9, 4 if pipe == 0 else -12), ha="left",
                         fontsize=8, color=INK)
    ax1.set_xlabel("N² (number of PEs)", color=MUTED, fontsize=9)
    ax1.set_ylabel("area (1000 µm², sky130 HD)", color=MUTED, fontsize=9)
    ax1.set_xlim(-3, 74)
    ax1.set_ylim(bottom=0)
    ax1.set_xticks([n * n for n in sizes], [f"{n * n}\n(N={n})" for n in sizes])
    ax1.set_title("Area grows linearly with the PE count", color=INK, fontsize=10, loc="left")
    style(ax1)

    width = 0.38
    for k, (pipe, (label, color)) in enumerate(SERIES.items()):
        xs = [i + (k - 0.5) * width for i in range(len(sizes))]
        ys = [data[pipe][n][1] for n in sizes]
        bars = ax2.bar(xs, ys, width=width - 0.04, color=color, label=label)
        for b, y in zip(bars, ys):
            ax2.annotate(f"{y:.0f}", (b.get_x() + b.get_width() / 2, y),
                         textcoords="offset points", xytext=(0, 3), ha="center",
                         fontsize=8, color=INK)
    ax2.set_xticks(range(len(sizes)), [f"N={n}" for n in sizes])
    ax2.set_ylabel("core Fmax (MHz, flop → flop)", color=MUTED, fontsize=9)
    gains = [data[1][n][1] / data[0][n][1] for n in sizes]
    ax2.set_title(f"Registering the product raises Fmax {min(gains):.1f}-{max(gains):.1f}x",
                  color=INK, fontsize=10, loc="left")
    style(ax2)

    handles, labels = ax2.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=2, frameon=False, fontsize=9,
               labelcolor=INK)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    os.makedirs(RESULTS, exist_ok=True)
    path = os.path.join(RESULTS, "synth_area_fmax.png")
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    print(f"Plot written to {os.path.abspath(path)}")


if __name__ == "__main__":
    main()
