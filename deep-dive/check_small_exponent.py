#!/usr/bin/env python3
"""Run and fail-closed classify the stock-kernel small-exponent control."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


TOOLCHAIN = "leanprover/lean4:v4.32.2"
LEAN_COMMIT = "f3b06c705e6c85f5314019d5d3baab0fec5b580c"
PROBE = Path(__file__).with_name("probes") / "SmallExponent.lean"
EXPONENTS = (4, 8, 16)
FIELD_SIZE = 2_130_706_433
LARGE_EXPONENT = FIELD_SIZE**6
FORTY_GIB_BITS = 40 * 1024**3 * 8
EXPANDED_FOUR = (
    "(((PowTrace.one.mul PowTrace.atom).mul PowTrace.atom).mul "
    "PowTrace.atom).mul PowTrace.atom"
)


class SmallExponentError(RuntimeError):
    pass


def kernel_count(block: str, name: str) -> int:
    matches = re.findall(
        rf"^    \[kernel\] {re.escape(name)} ↦ (\d+)$", block, re.MULTILINE
    )
    if len(matches) != 1:
        raise SmallExponentError(
            f"expected exactly one kernel count for {name}, found {len(matches)}"
        )
    return int(matches[0])


def classify_output(output: str) -> list[dict[str, int]]:
    markers = list(re.finditer(r"^EXPONENT (4|8|16)$", output, re.MULTILINE))
    if [int(marker.group(1)) for marker in markers] != list(EXPONENTS):
        raise SmallExponentError("missing or reordered exponent markers")

    expansion_marker = re.search(r"^EXPANDED 4$", output, re.MULTILINE)
    if expansion_marker is None or expansion_marker.start() <= markers[-1].start():
        raise SmallExponentError("missing or misplaced expansion marker")
    large_marker = re.search(r"^LARGE LOWER BOUND$", output, re.MULTILINE)
    if large_marker is None or large_marker.start() <= expansion_marker.start():
        raise SmallExponentError("missing or misplaced large-bound marker")

    rows: list[dict[str, int]] = []
    ends = [marker.start() for marker in markers[1:]] + [expansion_marker.start()]
    for exponent, marker, end in zip(EXPONENTS, markers, ends):
        block = output[marker.end() : end]
        if block.count("  [kernel] unfolded declarations") != 1:
            raise SmallExponentError(
                f"exponent {exponent} did not contain exactly one kernel section"
            )
        row = {
            "exponent": exponent,
            "npowRec._f": kernel_count(block, "npowRec._f"),
            "npowRec.match_1": kernel_count(block, "npowRec.match_1"),
            "Mul.mul": kernel_count(block, "Mul.mul"),
        }
        expected_visits = exponent + 1
        if row["npowRec._f"] != expected_visits:
            raise SmallExponentError(
                f"exponent {exponent}: npowRec._f={row['npowRec._f']}, "
                f"expected {expected_visits}"
            )
        if row["npowRec.match_1"] != expected_visits:
            raise SmallExponentError(
                f"exponent {exponent}: npowRec.match_1="
                f"{row['npowRec.match_1']}, expected {expected_visits}"
            )
        if row["Mul.mul"] != exponent:
            raise SmallExponentError(
                f"exponent {exponent}: Mul.mul={row['Mul.mul']}, "
                f"expected {exponent}"
            )
        rows.append(row)

    expansion_lines = [
        line.strip()
        for line in output[expansion_marker.end() : large_marker.start()].splitlines()
        if line.strip()
    ]
    if expansion_lines != [EXPANDED_FOUR]:
        raise SmallExponentError(
            f"unexpected exponent-four expansion: {expansion_lines!r}"
        )
    large_lines = [
        line.strip()
        for line in output[large_marker.end() :].splitlines()
        if line.strip()
    ]
    expected_large_lines = [
        f"fieldSize = {FIELD_SIZE}",
        f"fieldSize^6 nodes = {LARGE_EXPONENT}",
        f"40 GiB bits = {FORTY_GIB_BITS}",
    ]
    if large_lines != expected_large_lines:
        raise SmallExponentError(f"unexpected large-bound output: {large_lines!r}")
    if LARGE_EXPONENT <= FORTY_GIB_BITS:
        raise SmallExponentError("large exponent does not exceed the 40 GiB bit bound")
    return rows


def print_report(rows: list[dict[str, int]]) -> None:
    print("SMALL-EXPONENT KERNEL CONTROL")
    print("  rfl: atom^(n + 1) = atom^n * atom")
    print("  theorem: mulNodes(atom^n) = n for every natural n")
    print("  exponent | npowRec case visits | multiplication unfolds")
    for row in rows:
        print(
            f"  {row['exponent']:>8} | {row['npowRec._f']:>19} | "
            f"{row['Mul.mul']:>22}"
        )
    print(f"  exponent 4 expands to: {EXPANDED_FOUR}")
    print(
        "PASS: explicit npowRec expansion has n nodes: Theta(n) output, Omega(n) work"
    )
    bits_per_gib = 8 * 1024**3
    minimum_gib = (LARGE_EXPONENT + bits_per_gib - 1) // bits_per_gib
    factor_over_forty_gib = LARGE_EXPONENT // FORTY_GIB_BITS
    print("\nLARGE-EXPONENT SAFE RESOURCE LOWER BOUND")
    print(f"  fieldSize^6 multiplication nodes: {LARGE_EXPONENT:,}")
    print(f"  40 GiB contains: {FORTY_GIB_BITS:,} bits")
    print(
        "  at an impossible one bit/node, explicit expansion needs at least: "
        f"{minimum_gib:,} GiB"
    )
    print(f"  that is more than {factor_over_forty_gib:,} times 40 GiB")
    print("PASS: a complete explicit expansion cannot fit in 40 GiB")


def archived_resource_evidence() -> tuple[float, float]:
    summary_path = Path(__file__).with_name("evidence") / "summary.json"
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        bounds = summary["bounds"]
        arms = summary["factorial_original_condition_order"]
        control = arms["none"]
        trace = arms["trace"]
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise SmallExponentError(f"cannot read archived resource evidence: {error}")
    if bounds["process_group_rss_watchdog_gib"] != 8:
        raise SmallExponentError("archived replay did not use the expected 8 GiB bound")
    if control["classification"] != "accepted":
        raise SmallExponentError("archived no-transport control was not accepted")
    if trace["classification"] != "deep_recursion":
        raise SmallExponentError("archived trace arm did not deep-recurse")
    control_mib = float(control["peak_process_group_rss_mib"])
    trace_mib = float(trace["peak_process_group_rss_mib"])
    if control_mib != 3264.953 or trace_mib != 7848.438:
        raise SmallExponentError("archived replay resource values drifted")
    return control_mib, trace_mib


def print_archived_resource_evidence(control_mib: float, trace_mib: float) -> None:
    print("\nARCHIVED BOUNDED COMPARATOR OBSERVATION")
    print("  measurement: whole-pipeline process-group peak RSS")
    print(f"  no concrete transport: {control_mib / 1024:.3f} GiB, accepted")
    print(f"  trace transport:       {trace_mib / 1024:.3f} GiB, deep recursion")
    print("  cap: 8 GiB; this does not attribute all pipeline memory to npowRec")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verbose", action="store_true", help="print Lean's complete diagnostics"
    )
    args = parser.parse_args()

    elan = shutil.which("elan")
    if elan is None:
        raise SmallExponentError(
            "elan is required; install it and the leanprover/lean4:v4.32.2 toolchain"
        )
    version = subprocess.run(
        [elan, "run", TOOLCHAIN, "lean", "--version"],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
    )
    if version.returncode != 0:
        raise SmallExponentError(
            f"the {TOOLCHAIN} toolchain is not installed; run "
            f"`elan toolchain install {TOOLCHAIN}`"
        )
    if "version 4.32.2," not in version.stdout or LEAN_COMMIT not in version.stdout:
        raise SmallExponentError(f"unexpected Lean version: {version.stdout.strip()}")

    env = {**os.environ, "LEAN_NUM_THREADS": "1"}
    result = subprocess.run(
        [elan, "run", TOOLCHAIN, "lean", PROBE],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        env=env,
    )
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        raise SmallExponentError(f"Lean exited {result.returncode}")
    if args.verbose:
        print("--- complete stock-kernel diagnostics ---")
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        print("--- end diagnostics ---\n")
    print_report(classify_output(result.stdout))
    print_archived_resource_evidence(*archived_resource_evidence())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SmallExponentError, subprocess.TimeoutExpired) as error:
        raise SystemExit(f"small-exponent control failed closed: {error}")
