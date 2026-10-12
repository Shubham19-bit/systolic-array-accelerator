"""
plot_perf.py - Figures for the performance model (called by perf_model.py).

  results/utilization_vs_size.png   PE utilization vs array size, one line per layer
  results/roofline_n<N>.png         roofline of the largest array with every layer on it
"""
from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from plot_style import CATEGORICAL, INK, MUTED, SURFACE, style  # noqa: E402

# what makes the three lowest lines low (each is derived in the README)
NOTES = {
    "ResNet18 conv1": "conv1: K = 147, fill/drain dominates",
    "ResNet18 conv5_x": "conv5_x: 49 of 64 columns used",
    "ResNet18 fc": "fc: 1 of n columns used",
}


def utilization_plot(rows, sizes, layers, path):
    fig, ax = plt.subplots(figsize=(9, 5.2), facecolor=SURFACE)
    for i, name in enumerate(layers):
        ys = [p["util"] * 100 for (lname, n, p) in rows if lname == name]
        ax.plot(sizes, ys, color=CATEGORICAL[i], linewidth=2, marker="o", markersize=7,
                markeredgecolor=SURFACE, markeredgewidth=2, label=name)
        if name in NOTES:
            ax.annotate(NOTES[name], (sizes[-1], ys[-1]), textcoords="offset points",
                        xytext=(9, 0), va="center", fontsize=8, color=INK)
    ax.set_xscale("log", base=2)
    ax.set_xticks(sizes, [f"{s}x{s}" for s in sizes])
    ax.minorticks_off()
    ax.set_xlim(sizes[0] / 1.15, sizes[-1] * 2.9)
    ax.set_ylim(0, 105)
    ax.set_xlabel("array size")
    ax.set_ylabel("PE utilization (%)")
    ax.set_title("Utilization falls when K is small or the layer does not fill the array",
                 color=INK, fontsize=10, loc="left")
    style(ax)
    fig.legend(*ax.get_legend_handles_labels(), loc="lower center", ncol=4, frameon=False,
               fontsize=8, labelcolor=INK)
    fig.tight_layout(rect=(0, 0.1, 1, 1))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def roofline_plot(rows, n, freq, layers, path):
    peak = 2 * n * n * freq / 1e3                 # GOPS
    bw = 2 * n * freq / 1e3                       # GB/s of operand input (2n bytes per cycle)
    knee = peak / bw                              # = n ops/byte
    xs = np.logspace(-0.8, 2.4, 200)
    fig, ax = plt.subplots(figsize=(8, 5.2), facecolor=SURFACE)
    ax.loglog(xs, np.minimum(peak, bw * xs), color=INK, linewidth=2,
              label=f"roofline: {peak:.0f} GOPS peak, {bw:.1f} GB/s operand input")
    ax.axvline(knee, color=MUTED, linewidth=1, linestyle=(0, (4, 3)))
    ax.text(knee * 1.06, peak * 1.2, f"knee: {knee:.0f} ops/byte", fontsize=8, color=MUTED)
    ax.text(0.3, peak * 0.62, "bandwidth-bound\n(slope)", fontsize=8, color=MUTED)
    ax.text(knee * 1.5, peak * 0.62, "compute-bound", fontsize=8, color=MUTED)
    for i, name in enumerate(layers):
        for (lname, nn, p) in rows:
            if lname == name and nn == n:
                ax.plot(p["intensity"], p["gops"], "o", color=CATEGORICAL[i], markersize=9,
                        markeredgecolor=SURFACE, markeredgewidth=2, label=name)
                if name == "ResNet18 fc":
                    ax.annotate("fc: matrix-vector,\n1 of n columns", (p["intensity"], p["gops"]),
                                textcoords="offset points", xytext=(12, -22), fontsize=8,
                                color=INK)
    # zoom on the cluster of large layers: they are within a few percent of each other,
    # so on the log-log axes their dots hide each other
    big = [(i, p) for i, name in enumerate(layers) for (lname, nn, p) in rows
           if lname == name and nn == n and p["intensity"] > 5]
    if big:
        xs_b = [p["intensity"] for _, p in big]
        ys_b = [p["gops"] for _, p in big]
        inset = ax.inset_axes([0.50, 0.07, 0.46, 0.40])
        inset.set_facecolor(SURFACE)
        inset.plot(xs, np.minimum(peak, bw * xs), color=INK, linewidth=1.5)
        for i, p in big:
            inset.plot(p["intensity"], p["gops"], "o", color=CATEGORICAL[i], markersize=6,
                       markeredgecolor=SURFACE, markeredgewidth=1.2)
        inset.set_xlim(min(xs_b) - 2.5, knee + 1.5)
        inset.set_ylim(min(ys_b) - 25, peak * 1.03)
        inset.tick_params(labelsize=7, colors=MUTED)
        for side in ("top", "right"):
            inset.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            inset.spines[side].set_color(MUTED)
        inset.set_title("zoom: large layers (linear axes)", fontsize=7, color=MUTED, loc="left")
    ax.set_xlim(0.15, 250)
    ax.set_ylim(2.5, peak * 1.6)
    ax.set_xlabel("arithmetic intensity (ops per byte streamed in and out)")
    ax.set_ylabel("achieved GOPS")
    ax.set_title(f"Roofline, {n}x{n} array at {freq:g} MHz: every layer is on or just under the bandwidth slope",
                 color=INK, fontsize=10, loc="left")
    style(ax)
    ax.grid(which="minor", color="#f0efec", linewidth=0.5)
    fig.legend(*ax.get_legend_handles_labels(), loc="lower center", ncol=3, frameon=False,
               fontsize=8, labelcolor=INK)
    fig.tight_layout(rect=(0, 0.13, 1, 1))
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def make_plots(rows, sizes, freq, layers, results_dir):
    os.makedirs(results_dir, exist_ok=True)
    names = list(layers)
    utilization_plot(rows, sizes, names, os.path.join(results_dir, "utilization_vs_size.png"))
    n = sizes[-1]
    roofline_plot(rows, n, freq, names, os.path.join(results_dir, f"roofline_n{n}.png"))
    print(f"Plots written to {os.path.abspath(results_dir)}")
