"""
bug_injection.py - Mutation check of the verification environment.

For every bug in BUGS this script copies rtl/, tb/ and model/ into a temporary directory,
applies the one-line RTL mutation to the COPY (the real sources are never touched), runs
the cocotb and the pyuvm regressions there and records which tests fail. A bug that no
test catches is a hole in the bench (or an equivalent mutant) and must be explained.

Usage:  python analysis/bug_injection.py [--n 4] [--out docs/bug_injection.md]
Exit status 1 if the unmutated design fails, or any bug is not caught.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COPIED = ("rtl", "tb", "model")
IGNORE = shutil.ignore_patterns("sim_build*", "results.xml", "__pycache__", "*.fst", "*.vcd")
SUITES = (("cocotb", "tb/cocotb"), ("pyuvm", "tb/pyuvm"))
TIMEOUT_S = 600


@dataclass(frozen=True)
class Bug:
    name: str
    file: str
    old: str
    new: str
    why: str            # what the mutation models / why it is interesting


BUGS = (
    Bug("skew lane 2 delay = 1", "rtl/skew_buffer.sv",
        "assign dout[i*DW +: DW] = sr[i*DW-1 -: DW];",
        "assign dout[i*DW +: DW] = (i == 2) ? sr[DW-1:0] : sr[i*DW-1 -: DW];",
        "operands meet in the wrong PE (needs N >= 3 to exist)"),
    Bug("accumulator effectively 16 bits", "rtl/pe.sv",
        "acc   <= acc + prod_ext;",
        "acc   <= AW'(16'(acc + prod_ext));",
        "signed 16-bit accumulator: overflows for large K / extreme values"),
    Bug("skew buffer ignores global en", "rtl/sa_top.sv",
        "u_skew_a (\n        .clk (clk), .rst_n (rst_n), .en (en), .clr (clr),",
        "u_skew_a (\n        .clk (clk), .rst_n (rst_n), .en (1'b1), .clr (clr),",
        "stalls are no longer safe: skew advances while the array is frozen"),
    Bug("clr asserted while streaming output", "rtl/sa_top.sv",
        "assign clr = (state == S_CLEAR);",
        "assign clr = (state == S_CLEAR) || (state == S_OUTPUT && row_idx == 1);",
        "accumulators wiped before all rows are read out"),
    Bug("DRAIN one cycle too short (PIPE cycle forgotten)", "rtl/sa_top.sv",
        "((N > 1) ? 2 * N - 2 : 1) + PIPE;",
        "((N > 1) ? 2 * N - 2 : 1) + PIPE - 1;",
        "last products are never added to the accumulators: wrong C"),
    Bug("DRAIN one cycle too long", "rtl/sa_top.sv",
        "((N > 1) ? 2 * N - 2 : 1) + PIPE;",
        "((N > 1) ? 2 * N - 2 : 1) + PIPE + 1;",
        "functionally correct but one cycle slower: only a latency check can see it"),
    Bug("product register ignores en", "rtl/pe.sv",
        "else if (en)  prod_r <= prod;",
        "else          prod_r <= prod;",
        "pipeline register not frozen by a stall: a pending product is overwritten"),
    Bug("tlast on the wrong row", "rtl/sa_top.sv",
        "assign m_axis_tlast  = (state == S_OUTPUT) && (row_idx == ROW_LAST);",
        "assign m_axis_tlast  = (state == S_OUTPUT) && (row_idx == 0);",
        "AXI4-Stream framing error"),
    Bug("output row advances without tready", "rtl/sa_top.sv",
        "if (out_fire) begin\n                        if (row_idx == ROW_LAST) begin",
        "if (m_axis_tvalid) begin\n                        if (row_idx == ROW_LAST) begin",
        "rows lost under back-pressure (invisible when tready is always 1)"),
    Bug("tready high during DRAIN", "rtl/sa_top.sv",
        "assign s_axis_tready = (state == S_LOAD);",
        "assign s_axis_tready = (state == S_LOAD) || (state == S_DRAIN);",
        "next job's first beat is accepted into the current job"),
    Bug("clr does not clear the accumulator", "rtl/pe.sv",
        "end else if (clr) begin\n            a_out <= '0;\n            b_out <= '0;\n"
        "            acc   <= '0;\n",
        "end else if (clr) begin\n            a_out <= '0;\n            b_out <= '0;\n",
        "second job accumulates on top of the first (single-job tests cannot see it)"),
    Bug("PE forwards A and B swapped", "rtl/pe.sv",
        "a_out <= a_in;\n            b_out <= b_in;",
        "a_out <= b_in;\n            b_out <= a_in;",
        "dataflow error between neighbouring PEs"),
)


def failed_tests(results_xml: str) -> tuple[list[str], int]:
    """(names of failing tests, total number of tests) from a cocotb results.xml."""
    cases = list(ET.parse(results_xml).getroot().iter("testcase"))
    failed = [tc.get("name", "?") for tc in cases
              if tc.find("failure") is not None or tc.find("error") is not None]
    return failed, len(cases)


def run_suite(work: str, rel: str, n: int) -> tuple[str, list[str], int]:
    """Returns (status, failing tests, total tests); status in {"ran", "hang", "error"}."""
    d = os.path.join(work, rel)
    try:
        subprocess.run(["make", f"N={n}"], cwd=d, capture_output=True, text=True,
                       timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "hang", [], 0
    xml = os.path.join(d, "results.xml")
    if not os.path.exists(xml):
        return "error", [], 0        # did not compile / simulator died: not a detection
    return ("ran", *failed_tests(xml))


Result = dict[str, tuple[str, list[str], int]]


def run_variant(bug: Bug | None, n: int) -> Result:
    with tempfile.TemporaryDirectory(prefix="sa_bug_") as work:
        for d in COPIED:
            shutil.copytree(os.path.join(ROOT, d), os.path.join(work, d), ignore=IGNORE)
        if bug:
            path = os.path.join(work, bug.file)
            with open(path) as f:
                text = f.read()
            assert text.count(bug.old) == 1, f"mutation site not unique/found: {bug.name}"
            with open(path, "w") as f:
                f.write(text.replace(bug.old, bug.new))
        return {name: run_suite(work, rel, n) for name, rel in SUITES}


def describe(status: tuple[str, list[str], int]) -> str:
    kind, failed, total = status
    if kind == "hang":
        return "HANG (timeout)"
    if kind == "error":
        return "BUILD/RUN ERROR"
    if not failed:
        return "-"
    return f"all ({total})" if len(failed) == total else ", ".join(failed)


def caught(result: Result) -> bool:
    return any(kind == "hang" or (kind == "ran" and failed)
               for kind, failed, _ in result.values())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=4, help="array size to simulate")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "bug_injection.md"))
    args = ap.parse_args()

    print(f"Baseline (no bug), N={args.n} ...", flush=True)
    base = run_variant(None, args.n)
    if any(kind != "ran" or failed for kind, failed, _ in base.values()):
        print("Baseline FAILED - fix the design/bench first:", base)
        return 1

    rows, all_caught = [], True
    for bug in BUGS:
        print(f"Injecting: {bug.name} ...", flush=True)
        res = run_variant(bug, args.n)
        is_caught = caught(res)
        all_caught &= is_caught
        rows.append((bug, res, is_caught))
        print(f"   {'CAUGHT' if is_caught else 'NOT CAUGHT'}  "
              f"cocotb: {describe(res['cocotb'])} | pyuvm: {describe(res['pyuvm'])}")

    lines = [f"# Bug-injection results (N={args.n})", "",
             "Each row is a one-line RTL mutation applied to a copy of the design. "
             "'Caught by' lists the failing tests (cocotb | pyuvm). "
             "Generated by `analysis/bug_injection.py` (`make bugs`).", "",
             "| # | Injected bug | Why it matters | Caught by (cocotb) | Caught by (pyuvm) |",
             "|---|---|---|---|---|"]
    for i, (bug, res, is_caught) in enumerate(rows, 1):
        flag = "" if is_caught else " **NOT CAUGHT**"
        lines.append(f"| {i} | {bug.name} (`{bug.file}`) | {bug.why} | "
                     f"{describe(res['cocotb'])} | {describe(res['pyuvm'])}{flag} |")
    n_caught = sum(c for _, _, c in rows)
    lines += ["", f"**{n_caught}/{len(rows)} injected bugs caught.**", ""]
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w") as f:
        f.write("\n".join(lines))
    print(f"\n{n_caught}/{len(rows)} bugs caught; report written to {args.out}")
    return 0 if all_caught else 1


if __name__ == "__main__":
    sys.exit(main())
