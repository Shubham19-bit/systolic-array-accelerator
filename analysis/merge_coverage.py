"""
merge_coverage.py - Merge the per-test functional-coverage files and check closure.

Every pyuvm test writes results/cov_<test>_N<N>.json (see SaCoverage in
tb/pyuvm/test_sa_pyuvm.py). Different tests hit different bins (the no-stall test
covers "bp 0", the stress test the small stall buckets, the stall-pattern test the long
gaps and holds), so closure is judged on the union over the regression, per array size
N - the same idea as merging coverage databases in a commercial flow.

Usage:  python analysis/merge_coverage.py        (exit status 1 if any bin is empty)
"""
from __future__ import annotations

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")


def load(pattern: str) -> dict[int, dict]:
    """N -> {"tests": [...], "hits": {bin: count}} merged over all matching files."""
    merged: dict[int, dict] = {}
    for path in sorted(glob.glob(pattern)):
        with open(path) as f:
            d = json.load(f)
        m = merged.setdefault(d["N"], {"tests": [], "hits": {}})
        m["tests"].append(d["test"])
        for name, count in d["hits"].items():
            m["hits"][name] = m["hits"].get(name, 0) + count
    return merged


def main() -> int:
    merged = load(os.path.join(RESULTS, "cov_*_N*.json"))
    if not merged:
        print("No coverage files found - run `make uvm` (or `make cov`) first.")
        return 1

    sizes = sorted(merged)
    bins = list(merged[sizes[0]]["hits"])
    print(f"{'bin':<18}" + "".join(f"{'N=' + str(n):>8}" for n in sizes))
    for b in bins:
        print(f"{b:<18}" + "".join(f"{merged[n]['hits'].get(b, 0):>8}" for n in sizes))

    ok = True
    print()
    for n in sizes:
        hits = merged[n]["hits"]
        missing = [b for b in bins if hits.get(b, 0) == 0]
        print(f"N={n}: {len(bins) - len(missing)}/{len(bins)} bins hit "
              f"(tests: {', '.join(merged[n]['tests'])})")
        if missing:
            ok = False
            print(f"   UNCOVERED: {missing}")
    print("Coverage closure: " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
