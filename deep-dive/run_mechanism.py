#!/usr/bin/env python3
"""Reproduce and classify the exact kernel reduction path under hard bounds."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import resource
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Mapping, Sequence

from check_small_exponent import (
    SmallExponentError,
    classify_output as classify_small_exponent,
    print_report as print_small_exponent_report,
)


COMPPOLY = Path("/home/runner/CompPoly")
COMPARATOR_ROOT = Path("/home/runner/comparator")
MATHLIB = COMPPOLY / ".lake/packages/mathlib"
LEAN4EXPORT = (
    COMPARATOR_ROOT / ".lake/packages/lean4export/.lake/build/bin/lean4export"
)
LEAN_SOURCE = Path("/home/runner/lean4-instrumented")
PATCH = Path("/repro/deep-dive/patches/lean-4.32.2-kernel-probe.patch")
DIAGNOSTIC = Path("/repro/deep-dive/probes/DiagnosticReplay.lean")
SMALL_EXPONENT = Path("/repro/deep-dive/probes/SmallExponent.lean")
INSTRUMENTED_LEAN = Path(os.environ["REPRO_INSTRUMENTED_LEAN"])

COMPPOLY_REV = "6133f9f796707c438d0a614f97dc218ae976ab8f"
COMPARATOR_REV = os.environ["REPRO_COMPARATOR_REV"]
LEAN4EXPORT_REV = os.environ["REPRO_LEAN4EXPORT_REV"]
LEAN_SOURCE_REV = os.environ["REPRO_LEAN_SOURCE_REV"]
MATHLIB_REV = "905b95818eb32af7874a58b427f50c1711a5e96c"
PATCH_SHA256 = os.environ["REPRO_LEAN_PATCH_SHA256"]
EXPORT_SHA256 = os.environ["REPRO_OLD_EXPORT_SHA256"]
MODULE = "CompPoly.Fields.KoalaBear.Ext6.SexticIrreducible"
THEOREM = "KoalaBear.sexticPoly_irreducible"

MAX_MEMORY_BYTES = 8 * 1024**3
MAX_STACK_BYTES = 8 * 1024**2
MAX_EXPORT_BYTES = 256 * 1024**2
BUILD_TIMEOUT_SECONDS = 600.0
EXPORT_TIMEOUT_SECONDS = 120.0
REPLAY_TIMEOUT_SECONDS = 90.0


class ExperimentError(RuntimeError):
    pass


@dataclass(frozen=True)
class Result:
    returncode: int
    elapsed: float
    timed_out: bool
    output: str


def display(args: Sequence[object]) -> str:
    return shlex.join(str(arg) for arg in args)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def environment() -> dict[str, str]:
    return {**os.environ, "LEAN_NUM_THREADS": "1"}


def kill_group(process: subprocess.Popen[object]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run(
    args: Sequence[object],
    *,
    cwd: Path,
    timeout: float,
    print_command: bool = True,
) -> Result:
    if print_command:
        print(f"\n$ {display(args)}", flush=True)
    started = time.monotonic()
    process = subprocess.Popen(
        [str(arg) for arg in args],
        cwd=cwd,
        env=environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    timed_out = False
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_group(process)
        output, _ = process.communicate()
    return Result(process.returncode, time.monotonic() - started, timed_out, output or "")


def require_success(result: Result, label: str) -> None:
    if result.timed_out:
        raise ExperimentError(f"{label} exceeded its time bound")
    if result.returncode != 0:
        if result.output:
            print(result.output, file=sys.stderr)
        raise ExperimentError(f"{label} exited {result.returncode}")


def captured(args: Sequence[object], *, cwd: Path) -> str:
    result = run(args, cwd=cwd, timeout=30.0, print_command=False)
    require_success(result, display(args))
    return result.output.strip()


def cgroup_memory_limit() -> int:
    for path in (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ):
        try:
            raw = path.read_text(encoding="ascii").strip()
        except OSError:
            continue
        if raw == "max":
            raise ExperimentError("container has no cgroup memory limit")
        try:
            limit = int(raw)
        except ValueError as error:
            raise ExperimentError(f"invalid cgroup memory limit {raw!r}") from error
        if limit >= 1 << 60:
            raise ExperimentError("container has no effective cgroup memory limit")
        if limit > MAX_MEMORY_BYTES:
            raise ExperimentError("container memory limit exceeds 8 GiB")
        return limit
    raise ExperimentError("cannot read the container cgroup memory limit")


def assert_limits() -> None:
    memory_limit = cgroup_memory_limit()
    stack_soft, stack_hard = resource.getrlimit(resource.RLIMIT_STACK)
    for label, limit in (("soft", stack_soft), ("hard", stack_hard)):
        if limit == resource.RLIM_INFINITY or limit > MAX_STACK_BYTES:
            raise ExperimentError(
                f"process {label} stack limit is not bounded at 8 MiB"
            )

    swap_v2 = Path("/sys/fs/cgroup/memory.swap.max")
    memsw_v1 = Path("/sys/fs/cgroup/memory/memory.memsw.limit_in_bytes")
    if swap_v2.exists():
        if swap_v2.read_text(encoding="ascii").strip() != "0":
            raise ExperimentError("container cgroup permits swap")
    elif memsw_v1.exists():
        if int(memsw_v1.read_text(encoding="ascii").strip()) > memory_limit:
            raise ExperimentError("container cgroup permits swap")
    else:
        raise ExperimentError("cannot verify the container swap limit")


def assert_revision(path: Path, expected: str, label: str) -> None:
    actual = captured(["git", "rev-parse", "HEAD"], cwd=path)
    if actual != expected:
        raise ExperimentError(f"{label} is {actual}, expected {expected}")


def assert_pins() -> None:
    assert_revision(COMPARATOR_ROOT, COMPARATOR_REV, "Comparator")
    assert_revision(
        COMPARATOR_ROOT / ".lake/packages/lean4export",
        LEAN4EXPORT_REV,
        "lean4export",
    )
    assert_revision(LEAN_SOURCE, LEAN_SOURCE_REV, "instrumented Lean source")
    if sha256_file(PATCH) != PATCH_SHA256:
        raise ExperimentError("kernel instrumentation patch hash drift")
    actual_diff = subprocess.check_output(
        ["git", "diff", "--binary", "HEAD"], cwd=LEAN_SOURCE
    )
    if sha256_bytes(actual_diff) != PATCH_SHA256:
        raise ExperimentError("instrumented Lean checkout differs from the exact patch")
    version = captured([INSTRUMENTED_LEAN, "--version"], cwd=LEAN_SOURCE)
    if "version 4.32.2," not in version or f"commit {LEAN_SOURCE_REV}" not in version:
        raise ExperimentError(f"unexpected instrumented Lean version: {version}")


def prepare_old_revision() -> None:
    result = run(
        ["git", "switch", "--detach", COMPPOLY_REV],
        cwd=COMPPOLY,
        timeout=30.0,
    )
    require_success(result, "CompPoly checkout")
    assert_revision(COMPPOLY, COMPPOLY_REV, "CompPoly")
    dirty = captured(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=COMPPOLY
    )
    if dirty:
        raise ExperimentError(f"tracked CompPoly files are dirty:\n{dirty}")
    result = run(["lake", "clean", "CompPoly"], cwd=COMPPOLY, timeout=60.0)
    require_success(result, "CompPoly clean")
    result = run(
        ["lake", "--no-cache", "build", MODULE],
        cwd=COMPPOLY,
        timeout=BUILD_TIMEOUT_SECONDS,
    )
    require_success(result, "CompPoly target build")


def assert_linear_source_bridge() -> None:
    assert_revision(MATHLIB, MATHLIB_REV, "Mathlib")
    npow_source = (LEAN_SOURCE / "src/Init/Data/Zero.lean").read_text(
        encoding="utf-8"
    )
    npow_recurrence = """def npowRec [One M] [Mul M] : Nat → M → M
  | 0, _ => 1
  | n + 1, a => npowRec n a * a"""
    if npow_recurrence not in npow_source:
        raise ExperimentError("pinned npowRec recurrence does not match the probe")

    polynomial_source = (
        MATHLIB / "Mathlib/Algebra/Polynomial/Basic.lean"
    ).read_text(encoding="utf-8")
    polynomial_bridge = (
        "instance (priority := 1) pow : Pow R[X] ℕ where "
        "pow p n := npowRec n p"
    )
    if polynomial_bridge not in polynomial_source:
        raise ExperimentError("pinned Polynomial.pow no longer delegates to npowRec")


def run_small_exponent_control() -> None:
    version = captured(["lake", "env", "lean", "--version"], cwd=COMPPOLY)
    if "version 4.32.2," not in version or LEAN_SOURCE_REV not in version:
        raise ExperimentError(f"unexpected stock Lean version: {version}")
    result = run(
        ["lake", "env", "lean", SMALL_EXPONENT],
        cwd=COMPPOLY,
        timeout=30.0,
    )
    require_success(result, "small-exponent kernel control")
    try:
        rows = classify_small_exponent(result.output)
    except SmallExponentError as error:
        raise ExperimentError(f"small-exponent kernel control: {error}") from error
    print()
    print_small_exponent_report(rows)
    print("  bridge: Polynomial.pow p n := npowRec n p (pinned Mathlib source)")


def export_target(path: Path) -> None:
    args = ["lake", "env", LEAN4EXPORT, MODULE, "--", THEOREM]
    print(f"\n$ {display(args)} > {path}", flush=True)
    started = time.monotonic()

    def limit_export_file() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_EXPORT_BYTES, MAX_EXPORT_BYTES))

    with path.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            [str(arg) for arg in args],
            cwd=COMPPOLY,
            env=environment(),
            text=True,
            stdout=stream,
            stderr=subprocess.PIPE,
            start_new_session=True,
            preexec_fn=limit_export_file,
        )
        try:
            _, stderr = process.communicate(timeout=EXPORT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            kill_group(process)
            _, stderr = process.communicate()
            raise ExperimentError("lean4export exceeded its time bound")
    if stderr:
        print(stderr, end="" if stderr.endswith("\n") else "\n")
    if process.returncode != 0:
        raise ExperimentError(f"lean4export exited {process.returncode}")
    if path.stat().st_size > MAX_EXPORT_BYTES:
        raise ExperimentError("lean4export exceeded its file-size bound")
    actual_hash = sha256_file(path)
    if actual_hash != EXPORT_SHA256:
        raise ExperimentError(
            f"export SHA-256 {actual_hash} != expected {EXPORT_SHA256}"
        )
    print(
        f"exported {path.stat().st_size:,} bytes in "
        f"{time.monotonic() - started:.1f}s; SHA-256 verified"
    )


def scalar(text: str, name: str) -> int:
    matches = re.findall(rf"^{re.escape(name)} (\d+)$", text, re.MULTILINE)
    if len(matches) != 1:
        raise ExperimentError(f"expected exactly one {name} counter")
    return int(matches[0])


def unfold_counts(text: str) -> dict[str, int]:
    return {
        name: int(count)
        for count, name in re.findall(
            r"^FAILED_UNFOLD (\d+) (.+)$", text, re.MULTILINE
        )
    }


def watched_counts(text: str) -> dict[str, int]:
    return {
        name: int(count)
        for name, count in re.findall(
            r"^FAILED_UNFOLD_WATCH (.+) (\d+)$", text, re.MULTILINE
        )
    }


def require_marker(text: str, marker: str, label: str) -> None:
    if marker not in text:
        raise ExperimentError(f"missing {label} marker: {marker}")


def classify(result: Result) -> dict[str, object]:
    if result.timed_out:
        raise ExperimentError("instrumented replay timed out")
    if result.returncode <= 0:
        raise ExperimentError(
            f"instrumented replay exited {result.returncode}; expected a checked failure"
        )
    text = result.output
    if "target accepted" in text:
        raise ExperimentError("instrumented replay unexpectedly accepted the target")
    require_marker(text, "replaying dependencies", "dependency replay")
    require_marker(text, "dependencies accepted", "dependency acceptance")
    require_marker(text, "THEOREM_PHASE check-body", "target body check")
    require_marker(
        text,
        "STACK_COMPONENT deep recursion was detected at 'type checker: whnf'",
        "WHNF stack failure",
    )
    require_marker(text, "INFER_FAILURE_PHASE argument-defeq", "failure phase")
    require_marker(text, "INFER_FAILURE_APP App:Eq.mpr/4", "Eq.mpr argument four")
    require_marker(
        text,
        "last=App:CompPoly.RabinCert.dvd_X_pow_sub_X_of_runChain/",
        "trace certificate argument",
    )
    require_marker(
        text,
        "WATCHED_ROOT_GIVEN Dvd.dvd.{0} (Polynomial.{0} (ZMod KoalaBear.fieldSize)",
        "concrete ZMod endpoint",
    )
    require_marker(text, "ZMod.instField", "concrete ZMod field instance")
    require_marker(
        text,
        "WATCHED_ROOT_EXPECTED Dvd.dvd.{0} (Polynomial.{0} KoalaBear.Field",
        "KoalaBear.Field endpoint",
    )
    require_marker(text, "KoalaBear.instFieldField", "named KoalaBear field instance")

    max_whnf = scalar(text, "WHNF_MAX_NESTED_CALLS")
    unfolds = unfold_counts(text)
    watched = watched_counts(text)
    if max_whnf < 1_000:
        raise ExperimentError(f"WHNF recursion was unexpectedly shallow: {max_whnf}")
    for name in ("Polynomial.pow", "npowRec", "npowRec._f", "Nat.rec"):
        if unfolds.get(name, 0) <= 0:
            raise ExperimentError(f"missing positive polynomial-power unfold: {name}")
    zero_watches = (
        "Fintype.card",
        "Fintype.elems",
        "ZMod.card",
        "ZMod.fintype",
    )
    for name in zero_watches:
        if watched.get(name) != 0:
            raise ExperimentError(
                f"expected zero unfolds for {name}, got {watched.get(name)!r}"
            )
    if watched.get("Eq.mpr") != 0:
        raise ExperimentError("Eq.mpr unexpectedly unfolded")
    return {
        "max_whnf": max_whnf,
        "polynomial_pow": unfolds["Polynomial.pow"],
        "npowRec": unfolds["npowRec"],
        "npowRec._f": unfolds["npowRec._f"],
        "Nat.rec": unfolds["Nat.rec"],
        "watches": {name: watched[name] for name in zero_watches},
        "eq_mpr_unfolds": watched["Eq.mpr"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verbose", action="store_true", help="print the complete kernel trace"
    )
    args = parser.parse_args()

    assert_limits()
    assert_pins()
    prepare_old_revision()
    assert_linear_source_bridge()
    run_small_exponent_control()
    export_path = Path("/tmp/comppoly-old-sextic.ndjson")
    export_target(export_path)

    replay_args = [
        "lake",
        "env",
        INSTRUMENTED_LEAN,
        "--run",
        DIAGNOSTIC,
        export_path,
    ]
    result = run(
        replay_args,
        cwd=COMPARATOR_ROOT,
        timeout=REPLAY_TIMEOUT_SECONDS,
    )
    if args.verbose:
        print("\n--- complete instrumented trace ---")
        print(result.output, end="" if result.output.endswith("\n") else "\n")
        print("--- end trace ---")
    report = classify(result)

    print("\nEXACT KERNEL PATH REPRODUCED")
    print("  failure:  Eq.mpr argument 4 -> argument definitional equality")
    print("  endpoints: Polynomial (ZMod fieldSize) vs Polynomial KoalaBear.Field")
    print(
        "  recursion: "
        f"max WHNF depth {report['max_whnf']:,}; "
        f"npowRec._f {report['npowRec._f']:,}; Nat.rec {report['Nat.rec']:,}"
    )
    watches = report["watches"]
    print(
        "  cardinality unfolds: "
        + ", ".join(f"{name}={count}" for name, count in watches.items())
    )
    print(f"  Eq.mpr unfolds: {report['eq_mpr_unfolds']}")
    print("PASS: the observed recursion is polynomial exponentiation, not field enumeration")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExperimentError as error:
        raise SystemExit(f"mechanism experiment failed closed: {error}")
