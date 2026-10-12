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
# suite name -> (directory, extra make arguments)
SUITES = {
    "cocotb": ("tb/cocotb", []),
    "pyuvm": ("tb/pyuvm", []),
    "quant": ("tb/cocotb", ["TOP=quant_relu", "MODULE=test_quant", "QSTAGES=3"]),
    "layer": ("tb/cocotb", ["TOP=sa_layer", "MODULE=test_sa_layer", "QSTAGES=3"]),
    "gemm": ("tb/cocotb", ["TOP=sa_gemm", "MODULE=test_sa_gemm"]),
}
TIMEOUT_S = 600


@dataclass(frozen=True)
class Bug:
    name: str
    file: str
    old: str
    new: str
    why: str            # what the mutation models / why it is interesting
    suites: tuple[str, ...] = ("cocotb", "pyuvm")     # test suites that must notice it


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
    # ---- requantization stage (rtl/quant_relu.sv) and its integration (rtl/sa_layer.sv)
    Bug("quant: rounding dropped", "rtl/quant_relu.sv",
        "f[DW:1] + DW'(f[0]);", "f[DW:1];",
        "truncation instead of round-half-up: off by one on every tie",
        suites=("quant",)),
    Bug("quant: round bit inverted", "rtl/quant_relu.sv",
        "f[DW:1] + DW'(f[0]);", "f[DW:1] + DW'(!f[0]);",
        "rounds up exactly when it should round down", suites=("quant",)),
    Bug("quant: logical instead of arithmetic shift", "rtl/quant_relu.sv",
        "assign zs = z >>> cfg_shift;", "assign zs = z >> cfg_shift;",
        "negative values turn into large positives and saturate the wrong way",
        suites=("quant",)),
    Bug("quant: high saturation threshold off by one", "rtl/quant_relu.sv",
        "(sh >= SAT_HI)", "(sh > SAT_HI)",
        "sh = 127 with the round bit set becomes 128 and wraps to -128", suites=("quant",)),
    Bug("quant: asymmetric low saturation (-127)", "rtl/quant_relu.sv",
        "f[FW-2] ? {1'b1, {(DW-1){1'b0}}}",
        "f[FW-2] ? {1'b1, {(DW-2){1'b0}}, 1'b1}",
        "values below -128 clamp to -127", suites=("quant",)),
    Bug("quant: sign extension lost", "rtl/quant_relu.sv",
        "? '0 : {x[AW-1], x};", "? '0 : {1'b0, x};",
        "negative accumulators are treated as large positives", suites=("quant",)),
    Bug("quant: ReLU missing", "rtl/quant_relu.sv",
        "assign xr = (cfg_relu && x[AW-1]) ? '0 : {x[AW-1], x};",
        "assign xr = {x[AW-1], x};",
        "negative accumulators pass through", suites=("quant",)),
    Bug("quant: output register reloads while stalled", "rtl/quant_relu.sv",
        "end else if (adv) begin\n            m_axis_tvalid <= p2_valid;",
        "end else begin\n            m_axis_tvalid <= p2_valid;",
        "output changes / beats are lost under back-pressure", suites=("quant",)),
    Bug("quant: pipeline register not frozen by a stall", "rtl/quant_relu.sv",
        "end else if (adv) begin\n                    p1_valid <= s_axis_tvalid;",
        "end else begin\n                    p1_valid <= s_axis_tvalid;",
        "a beat inside the pipeline is overwritten while the output is stalled",
        suites=("quant",)),
    Bug("quant: tlast dropped", "rtl/quant_relu.sv",
        "m_axis_tlast <= p2_last;", "m_axis_tlast <= 1'b0;",
        "AXI4-Stream framing lost", suites=("quant",)),
    Bug("layer: output back-pressure ignored by the stage", "rtl/sa_layer.sv",
        ".m_axis_tready(m_axis_tready),\n        .m_axis_tdata(m_axis_tdata),",
        ".m_axis_tready(1'b1),\n        .m_axis_tdata(m_axis_tdata),",
        "rows lost when the consumer stalls", suites=("layer",)),
    Bug("layer: ReLU enable inverted", "rtl/sa_layer.sv",
        ".cfg_relu(cfg_relu),", ".cfg_relu(!cfg_relu),",
        "wiring error between configuration and stage", suites=("layer",)),
    Bug("layer: cfg_shift tied to 0", "rtl/sa_layer.sv",
        ".cfg_shift(cfg_shift),", ".cfg_shift(5'd0),",
        "scale ignored: everything saturates", suites=("layer",)),
    # ---- tiled GEMM controller (rtl/sa_gemm.sv)
    Bug("gemm: FIFO credit allows 3 entries", "rtl/sa_gemm.sv",
        "(used < 3'd2 || pop)", "(used < 3'd3 || pop)",
        "the 2-entry operand FIFO overflows: beats are overwritten", suites=("gemm",)),
    Bug("gemm: no read-ahead when the FIFO pops", "rtl/sa_gemm.sv",
        "(used < 3'd2 || pop)", "(used < 3'd2)",
        "functionally correct but the feed drops to 2 beats per 3 cycles: only the cycle count "
        "can see it", suites=("gemm",)),
    Bug("gemm: B read pointer not rewound for the next row block", "rtl/sa_gemm.sv",
        "                            b_base <= '0;\n                            a_base <= a_base + A_AW'(k_q);",
        "                            a_base <= a_base + A_AW'(k_q);",
        "second and later row blocks read the wrong B tiles", suites=("gemm",)),
    Bug("gemm: A block address off by one", "rtl/sa_gemm.sv",
        "a_base <= a_base + A_AW'(k_q);", "a_base <= a_base + A_AW'(k_q) + 1'b1;",
        "row blocks after the first read a shifted slice of A", suites=("gemm",)),
    Bug("gemm: tlast on every tile", "rtl/sa_gemm.sv",
        "assign m_axis_tlast  = c_tlast && last_tile && run;", "assign m_axis_tlast  = c_tlast && run;",
        "result stream is cut into one frame per tile", suites=("gemm",)),
    Bug("gemm: last tile detected from the row block only", "rtl/sa_gemm.sv",
        "assign last_tile = (oi == mb_q - MBW'(1)) && (oj == nb_q - NBW'(1));",
        "assign last_tile = (oi == mb_q - MBW'(1));",
        "job ends early when there is more than one column block", suites=("gemm",)),
    Bug("gemm: tile-last flag one beat early", "rtl/sa_gemm.sv",
        "rd_last_q <= (k == k_q - KW'(1));", "rd_last_q <= (k == k_q - KW'(2));",
        "sa_top sees tlast one beat too soon", suites=("gemm",)),
    Bug("gemm: FIFO pop+push mishandled at occupancy 1", "rtl/sa_gemm.sv",
        "2'b11: begin                                      // pop and push\n"
        "                    if (occ == 2'd1) begin",
        "2'b11: begin                                      // pop and push\n"
        "                    if (occ == 2'd2) begin",
        "a beat is duplicated and another lost in steady state", suites=("gemm",)),
    Bug("gemm: start restarts a running job", "rtl/sa_gemm.sv",
        "if (start && !run) begin", "if (start) begin",
        "a start pulse while busy corrupts the job", suites=("gemm",)),
    Bug("gemm: cycle counter off by one", "rtl/sa_gemm.sv",
        "gemm_cycles <= cyc + 1'b1;", "gemm_cycles <= cyc;",
        "performance counter wrong", suites=("gemm",)),
    Bug("gemm: reads continue after the last tile", "rtl/sa_gemm.sv",
        "assign issue     = run && !issue_done && (used", "assign issue     = run && (used",
        "extra operand beats are fetched and fed to the array", suites=("gemm",)),
)


def failed_tests(results_xml: str) -> tuple[list[str], int]:
    """(names of failing tests, total number of tests) from a cocotb results.xml."""
    cases = list(ET.parse(results_xml).getroot().iter("testcase"))
    failed = [tc.get("name", "?") for tc in cases
              if tc.find("failure") is not None or tc.find("error") is not None]
    return failed, len(cases)


def run_suite(work: str, name: str, n: int) -> tuple[str, list[str], int]:
    """Returns (status, failing tests, total tests); status in {"ran", "hang", "error"}."""
    rel, extra = SUITES[name]
    d = os.path.join(work, rel)
    try:
        subprocess.run(["make", f"N={n}", *extra], cwd=d, capture_output=True, text=True,
                       timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return "hang", [], 0
    xml = os.path.join(d, "results.xml")
    if not os.path.exists(xml):
        return "error", [], 0        # did not compile / simulator died: not a detection
    return ("ran", *failed_tests(xml))


Result = dict[str, tuple[str, list[str], int]]


def run_variant(bug: Bug | None, n: int, suites: tuple[str, ...]) -> Result:
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
        return {name: run_suite(work, name, n) for name in suites}


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
    ap.add_argument("--out", default=None,
                    help="report file (default docs/bug_injection.md; not written with --only)")
    ap.add_argument("--only", default="",
                    help="comma-separated suites (e.g. quant,layer): inject only bugs aimed at them")
    args = ap.parse_args()
    only = [x for x in args.only.split(",") if x]
    bugs = [b for b in BUGS if not only or set(b.suites) & set(only)]
    out = args.out or (None if only else os.path.join(ROOT, "docs", "bug_injection.md"))

    print(f"Baseline (no bug), N={args.n} ...", flush=True)
    base = run_variant(None, args.n, tuple(only) if only else tuple(SUITES))
    if any(kind != "ran" or failed for kind, failed, _ in base.values()):
        print("Baseline FAILED - fix the design/bench first:", base)
        return 1

    rows, all_caught = [], True
    for bug in bugs:
        print(f"Injecting: {bug.name} ...", flush=True)
        res = run_variant(bug, args.n, bug.suites)
        is_caught = caught(res)
        all_caught &= is_caught
        rows.append((bug, res, is_caught))
        print(f"   {'CAUGHT' if is_caught else 'NOT CAUGHT'}  "
              + " | ".join(f"{name}: {describe(r)}" for name, r in res.items()))

    lines = [f"# Bug-injection results (N={args.n})", "",
             "Each row is a one-line RTL mutation applied to a copy of the design. "
             "'Caught by' lists the failing tests of each suite. "
             "Generated by `analysis/bug_injection.py` (`make bugs`).", ""]
    groups = {}
    for bug, res, is_caught in rows:
        groups.setdefault(bug.suites, []).append((bug, res, is_caught))
    titles = {("cocotb", "pyuvm"): "Systolic array (sa_top, pe, skew buffer)",
              ("quant",): "Requantization stage (quant_relu)",
              ("layer",): "Layer integration (sa_layer)",
              ("gemm",): "Tiled GEMM controller (sa_gemm)"}
    counter = 0
    for suites, members in groups.items():
        lines += [f"## {titles.get(suites, ', '.join(suites))}", "",
                  "| # | Injected bug | Why it matters | " +
                  " | ".join(f"Caught by ({name})" for name in suites) + " |",
                  "|---|---|---|" + "---|" * len(suites)]
        for bug, res, is_caught in members:
            counter += 1
            flag = "" if is_caught else " **NOT CAUGHT**"
            cells = " | ".join(describe(res[name]) for name in suites)
            lines.append(f"| {counter} | {bug.name} (`{bug.file}`) | {bug.why} | {cells}{flag} |")
        lines.append("")
    n_caught = sum(c for _, _, c in rows)
    lines += [f"**{n_caught}/{len(rows)} injected bugs caught.**", ""]
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            f.write("\n".join(lines))
    print(f"\n{n_caught}/{len(rows)} bugs caught" + (f"; report written to {out}" if out else ""))
    return 0 if all_caught else 1


if __name__ == "__main__":
    sys.exit(main())
