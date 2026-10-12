"""
plot_style.py - Shared look of the project's figures (synthesis and performance plots).

Colors: the first slots of the reference categorical palette, in fixed order (an entity keeps
its color); ink/surface tokens for text; text is never drawn in a series color.
"""
from __future__ import annotations

SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1"
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300",
               "#6250d6", "#e34948"]


def style(ax):
    """Recessive grid and axes on the chart surface."""
    ax.set_facecolor(SURFACE)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.xaxis.label.set_color(MUTED)
    ax.yaxis.label.set_color(MUTED)
