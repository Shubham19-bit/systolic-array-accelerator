"""
perf_model.py - Analytical performance model of the systolic-array GEMM engine.

For a GEMM  C[M x Nc] = A[M x K] * B[K x Nc]  on an n x n array the work is
split into ceil(M/n) * ceil(Nc/n) output tiles. One tile costs (no stalls)

    cycles_tile = K + drain + n + 1   (K load, drain, n output, 1 clear)
    drain       = (2n - 2 for n > 1, 1 for n = 1) + pipe     pipe = 1: PE registers its product

which matches the RTL measurement  last_job_cycles = K + drain + n  (+1 CLEAR
cycle between back-to-back jobs). The model reports cycles, PE utilization, GOPS at a
given clock frequency and a simple roofline (compute vs. interface bandwidth).

Usage:
  python analysis/perf_model.py                      # table + plots for CNN layers
  python analysis/perf_model.py --freq 200 --sizes 4 8 16 32
  python analysis/perf_model.py --validate           # compare with results/sim_cycles_N*.json
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")

# Representative CNN layers lowered to GEMM with im2col:
#   M = output channels, K = in_ch * kh * kw, Nc = out_h * out_w
LAYERS = {
    "ResNet18 conv1":   dict(M=64,  K=3 * 7 * 7,   Nc=112 * 112),
    "ResNet18 conv2_x": dict(M=64,  K=64 * 3 * 3,  Nc=56 * 56),
    "ResNet18 conv3_x": dict(M=128, K=128 * 3 * 3, Nc=28 * 28),
    "ResNet18 conv4_x": dict(M=256, K=256 * 3 * 3, Nc=14 * 14),
    "ResNet18 conv5_x": dict(M=512, K=512 * 3 * 3, Nc=7 * 7),
    "ResNet18 fc":      dict(M=1000, K=512,        Nc=1),
    "BERT-base QKV":    dict(M=768 * 3, K=768,     Nc=128),
}


def tile_cycles(n: int, K: int, pipe: int = 1) -> int:
    drain = (2 * n - 2 if n > 1 else 1) + pipe    # +1: pipelined PE adds its product late
    return K + drain + n + 1


def gemm_perf(n: int, M: int, K: int, Nc: int, freq_mhz: float) -> dict:
    tiles = math.ceil(M / n) * math.ceil(Nc / n)
    cycles = tiles * tile_cycles(n, K)
    macs = M * K * Nc
    util = macs / (cycles * n * n)
    seconds = cycles / (freq_mhz * 1e6)
    gops = 2 * macs / seconds / 1e9
    peak_gops = 2 * n * n * freq_mhz * 1e6 / 1e9
    # interface traffic: each tile streams K beats of 2n bytes in, n*n*4 bytes out
    bytes_moved = tiles * (K * 2 * n + n * n * 4)
    intensity = 2 * macs / bytes_moved          # ops / byte
    return dict(tiles=tiles, cycles=cycles, macs=macs, util=util,
                gops=gops, peak_gops=peak_gops, intensity=intensity,
                time_us=seconds * 1e6)


def table(sizes, freq):
    print(f"\nClock = {freq} MHz\n")
    hdr = f"{'layer':<18}{'n':>4}{'tiles':>9}{'cycles':>12}{'util':>8}{'GOPS':>9}{'peak':>8}"
    print(hdr)
    print("-" * len(hdr))
    rows = []
    for name, d in LAYERS.items():
        for n in sizes:
            p = gemm_perf(n, freq_mhz=freq, **d)
            rows.append((name, n, p))
            print(f"{name:<18}{n:>4}{p['tiles']:>9}{p['cycles']:>12}"
                  f"{p['util']*100:>7.1f}%{p['gops']:>9.2f}{p['peak_gops']:>8.1f}")
        print()
    return rows


def plots(rows, sizes, freq):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed - skipping plots")
        return
    os.makedirs(RESULTS, exist_ok=True)

    # 1) utilization vs array size per layer
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for name in LAYERS:
        ys = [p["util"] * 100 for (lname, n, p) in rows if lname == name]
        ax.plot(sizes, ys, marker="o", label=name)
    ax.set_xscale("log", base=2)
    ax.set_xticks(sizes, [str(s) for s in sizes])
    ax.set_xlabel("array size n (n x n PEs)")
    ax.set_ylabel("PE utilization (%)")
    ax.set_title("Utilization vs. array size (output-stationary, no stalls)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS, "utilization_vs_size.png"), dpi=150)

    # 2) roofline for the largest array
    n = sizes[-1]
    peak = 2 * n * n * freq * 1e6 / 1e9
    bw_gbs = 2 * n * freq * 1e6 / 1e9       # input stream: 2n bytes per cycle
    xs = [2 ** e for e in range(-2, 12)]
    roof = [min(peak, bw_gbs * x) for x in xs]
    fig, ax = plt.subplots(figsize=(7, 4.5))
    ax.loglog(xs, roof, "k-", label=f"roofline (peak {peak:.1f} GOPS, {bw_gbs:.1f} GB/s in)")
    for (lname, nn, p) in rows:
        if nn == n:
            ax.plot(p["intensity"], p["gops"], "o", label=lname)
    ax.set_xlabel("arithmetic intensity (ops / byte)")
    ax.set_ylabel("achieved GOPS")
    ax.set_title(f"Roofline, {n}x{n} array @ {freq} MHz")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(os.path.join(RESULTS, f"roofline_n{n}.png"), dpi=150)
    print(f"Plots written to {os.path.abspath(RESULTS)}")


def validate():
    files = sorted(glob.glob(os.path.join(RESULTS, "sim_cycles_N*.json")))
    if not files:
        print("No simulation results found. Run `make` in tb/cocotb first.")
        return
    ok = True
    for f in files:
        data = json.load(open(f))
        for d in data:
            pred = tile_cycles(d["N"], d["K"], d.get("pipe", 1)) - 1   # RTL excludes CLEAR
            if pred != d["cycles"]:
                ok = False
                print(f"MISMATCH N={d['N']} K={d['K']}: model {pred}, RTL {d['cycles']}")
        print(f"{os.path.basename(f)}: {len(data)} jobs checked")
    print("Model matches RTL cycle counts." if ok else "Model/RTL mismatch - investigate!")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[4, 8, 16, 32])
    ap.add_argument("--freq", type=float, default=200.0, help="clock in MHz")
    ap.add_argument("--validate", action="store_true")
    args = ap.parse_args()
    if args.validate:
        validate()
        return
    rows = table(args.sizes, args.freq)
    plots(rows, args.sizes, args.freq)


if __name__ == "__main__":
    main()
