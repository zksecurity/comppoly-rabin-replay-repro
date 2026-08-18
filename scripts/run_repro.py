#!/usr/bin/env python3
"""Run a pinned CompPoly before/after pair through the real Comparator."""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import resource
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence


COMPPOLY = Path("/home/runner/CompPoly")
COMPARATOR_ROOT = Path("/home/runner/comparator")
COMPARATOR = COMPARATOR_ROOT / ".lake/build/bin/comparator"
LEAN4EXPORT = (
    COMPARATOR_ROOT / ".lake/packages/lean4export/.lake/build/bin/lean4export"
)
FAKE_LANDRUN = COMPARATOR_ROOT / "scripts/fake-landrun.sh"
CASE = Path("/repro/case")
CONFIG = CASE / "replay.json"

LEAN_VERSION = os.environ["REPRO_LEAN_VERSION"]
COMPARATOR_REV = os.environ["REPRO_COMPARATOR_REV"]
LEAN4EXPORT_REV = os.environ["REPRO_LEAN4EXPORT_REV"]
SUITE_NAME = os.environ["REPRO_SUITE_NAME"]
BASE_REV = os.environ["REPRO_BASE_COMPPOLY_REV"]
TARGET_REV = os.environ["REPRO_TARGET_COMPPOLY_REV"]
BASE_EXPECTATION = os.environ["REPRO_BASE_EXPECTATION"]
COMPARE_EXPORTS = os.environ.get("REPRO_COMPARE_EXPORTS", "false") == "true"

TARGETS = ("CompPoly.ReplayChallenge", "CompPoly.ReplaySolution")
EXPORT_MODULE = "CompPoly.Fields.KoalaBear.Ext6.SexticIrreducible"
EXPORT_THEOREM = "KoalaBear.sexticPoly_irreducible"
REPLAY_MARKER = "Running Lean default kernel on solution."
ACCEPT_MARKER = "Lean default kernel accepts the solution"
DONE_MARKER = "Your solution is okay!"
FAILING_DECLARATION = "KoalaBear.sexticPoly_irreducible"
WRAPPER_DECLARATION = (
    "CompPoly.RabinCert.irreducible_of_rabin_degree_six_of_card"
)
DEEP_RECURSION_SIGNATURE = (
    f"while replaying declaration '{FAILING_DECLARATION}':\n"
    "(kernel) deep recursion detected"
)
DECLARATION_PREFIXES = tuple(
    f'{{"{kind}":'
    for kind in ("axiom", "def", "opaque", "thm", "inductive", "quot")
)
MAX_ALLOWED_MEMORY_BYTES = 8 * 1024**3
MAX_ALLOWED_STACK_BYTES = 8 * 1024**2
COMMAND_TIMEOUT_SECONDS = 600.0
EXPORT_TIMEOUT_SECONDS = 90.0


class ReproductionError(RuntimeError):
    """The environment or an expected before/after invariant was wrong."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    elapsed_seconds: float
    timed_out: bool
    output: str


def display(args: Sequence[object]) -> str:
    return shlex.join(str(arg) for arg in args)


def runtime_environment(
    extra: Optional[Mapping[str, str]] = None,
) -> dict[str, str]:
    environment = {**os.environ, "LEAN_NUM_THREADS": "1"}
    if extra:
        environment.update(extra)
    return environment


def kill_process_group(process: subprocess.Popen[object]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run_checked(
    args: Sequence[object],
    *,
    cwd: Path,
    capture: bool = False,
    env: Optional[Mapping[str, str]] = None,
    timeout_seconds: float = COMMAND_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    print(f"\n$ {display(args)}", flush=True)
    process = subprocess.Popen(
        [str(arg) for arg in args],
        cwd=cwd,
        env=runtime_environment(env),
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        kill_process_group(process)
        output, _ = process.communicate()
        if output:
            print(output, end="" if output.endswith("\n") else "\n")
        raise ReproductionError(
            f"command exceeded the {timeout_seconds:.0f}s bound: {display(args)}"
        )
    result = subprocess.CompletedProcess(args, process.returncode, output)
    if result.returncode != 0:
        if result.stdout:
            print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        raise ReproductionError(
            f"command exited {result.returncode}: {display(args)}"
        )
    return result


def captured(args: Sequence[object], *, cwd: Path) -> str:
    return run_checked(args, cwd=cwd, capture=True).stdout.strip()


def cgroup_memory_limit() -> int:
    candidates = (
        Path("/sys/fs/cgroup/memory.max"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    )
    for path in candidates:
        try:
            raw = path.read_text(encoding="ascii").strip()
        except OSError:
            continue
        if raw == "max":
            raise ReproductionError(
                "the container has no memory cap; use the supplied repro.sh"
            )
        try:
            limit = int(raw)
        except ValueError as error:
            raise ReproductionError(f"invalid cgroup memory limit {raw!r}") from error
        # cgroup v1 represents an unlimited value with a very large integer.
        if limit >= 1 << 60:
            raise ReproductionError(
                "the container has no effective memory cap; use the supplied repro.sh"
            )
        if limit > MAX_ALLOWED_MEMORY_BYTES:
            raise ReproductionError(
                f"container memory cap is {limit / 1024**3:.1f} GiB; "
                "refusing to run above 8 GiB"
            )
        return limit
    raise ReproductionError("cannot read the container cgroup memory limit")


def assert_runtime_limits(memory_limit: int) -> None:
    stack_soft, stack_hard = resource.getrlimit(resource.RLIMIT_STACK)
    for label, limit in (("soft", stack_soft), ("hard", stack_hard)):
        if limit == resource.RLIM_INFINITY or limit > MAX_ALLOWED_STACK_BYTES:
            raise ReproductionError(
                f"process {label} stack limit is {limit}; expected at most 8 MiB"
            )

    # Docker's `--memory-swap` is total memory plus swap. With it equal to
    # `--memory`, cgroup v2 reports zero swap and v1 reports memsw == memory.
    swap_v2 = Path("/sys/fs/cgroup/memory.swap.max")
    memsw_v1 = Path("/sys/fs/cgroup/memory/memory.memsw.limit_in_bytes")
    try:
        if swap_v2.exists():
            if swap_v2.read_text(encoding="ascii").strip() != "0":
                raise ReproductionError("container cgroup permits swap")
        elif memsw_v1.exists():
            memsw = int(memsw_v1.read_text(encoding="ascii").strip())
            if memsw > memory_limit:
                raise ReproductionError("container cgroup permits swap")
        else:
            raise ReproductionError("cannot verify the container cgroup swap limit")
    except ValueError as error:
        raise ReproductionError("invalid cgroup swap limit") from error


def assert_pins() -> None:
    checks = (
        (COMPARATOR_ROOT, COMPARATOR_REV, "Comparator"),
        (COMPARATOR_ROOT / ".lake/packages/lean4export", LEAN4EXPORT_REV, "lean4export"),
    )
    for repository, expected, label in checks:
        actual = captured(["git", "rev-parse", "HEAD"], cwd=repository)
        if actual != expected:
            raise ReproductionError(f"{label} is {actual}, expected {expected}")

    version = captured(["lean", "--version"], cwd=COMPPOLY)
    if f"version {LEAN_VERSION}," not in version:
        raise ReproductionError(f"unexpected Lean version: {version}")
    print(version)


def prepare_revision(revision: str) -> float:
    run_checked(["git", "switch", "--detach", revision], cwd=COMPPOLY)
    actual = captured(["git", "rev-parse", "HEAD"], cwd=COMPPOLY)
    if actual != revision:
        raise ReproductionError(f"CompPoly is {actual}, expected {revision}")
    dirty = captured(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=COMPPOLY
    )
    if dirty:
        raise ReproductionError(f"tracked CompPoly files are dirty:\n{dirty}")

    shutil.copyfile(CASE / "ReplayChallenge.lean", COMPPOLY / "CompPoly/ReplayChallenge.lean")
    shutil.copyfile(CASE / "ReplaySolution.lean", COMPPOLY / "CompPoly/ReplaySolution.lean")

    run_checked(["lake", "clean", "CompPoly"], cwd=COMPPOLY)
    started = time.monotonic()
    run_checked(["lake", "--no-cache", "build", *TARGETS], cwd=COMPPOLY)
    return time.monotonic() - started


def comparator_environment() -> dict[str, str]:
    return runtime_environment(
        {
            "COMPARATOR_LANDRUN": str(FAKE_LANDRUN),
            "COMPARATOR_LEAN4EXPORT": str(LEAN4EXPORT),
        }
    )


def run_comparator(timeout_seconds: float) -> CommandResult:
    args = ["lake", "env", str(COMPARATOR), str(CONFIG)]
    print(f"\n$ {display(args)}", flush=True)
    started = time.monotonic()
    process = subprocess.Popen(
        args,
        cwd=COMPPOLY,
        env=comparator_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    timed_out = False
    try:
        output, _ = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_process_group(process)
        output, _ = process.communicate()

    elapsed = time.monotonic() - started
    output = output or ""
    print(output, end="" if output.endswith("\n") else "\n")
    if timed_out:
        print(f"bounded timeout after {elapsed:.1f}s")
    else:
        print(f"exit {process.returncode} after {elapsed:.1f}s")
    return CommandResult(process.returncode, elapsed, timed_out, output)


def require_acceptance(label: str, result: CommandResult) -> None:
    if (
        result.timed_out
        or result.returncode != 0
        or REPLAY_MARKER not in result.output
        or ACCEPT_MARKER not in result.output
        or DONE_MARKER not in result.output
    ):
        raise ReproductionError(
            f"{label} Comparator replay did not reach all required markers"
        )


def classify_pathological_base(result: CommandResult) -> str:
    if REPLAY_MARKER not in result.output:
        raise ReproductionError(
            "pathological base did not reach full kernel replay; "
            "this is a setup failure"
        )
    if ACCEPT_MARKER in result.output or DONE_MARKER in result.output:
        raise ReproductionError(
            "pathological base reached an acceptance marker; refusing to "
            "classify a later stall or failure as the replay pathology"
        )
    if result.timed_out:
        return "stalled in full kernel replay until the configured timeout"
    if (
        result.returncode > 0
        and DEEP_RECURSION_SIGNATURE in result.output
    ):
        return "hit the expected kernel deep-recursion signature"
    if result.returncode == 0:
        raise ReproductionError(
            "pathological base replay unexpectedly completed successfully"
        )
    raise ReproductionError(
        f"pathological base replay failed for an unrelated reason "
        f"(exit {result.returncode})"
    )


def export_closure(output_path: Path) -> None:
    args = [
        "lake",
        "env",
        str(LEAN4EXPORT),
        EXPORT_MODULE,
        "--",
        EXPORT_THEOREM,
    ]
    print(f"\n$ {display(args)} > {output_path}", flush=True)
    started = time.monotonic()
    with output_path.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            args,
            cwd=COMPPOLY,
            env=runtime_environment(),
            text=True,
            stdout=stream,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        try:
            _, stderr = process.communicate(timeout=EXPORT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            kill_process_group(process)
            _, stderr = process.communicate()
            raise ReproductionError(
                f"lean4export exceeded the {EXPORT_TIMEOUT_SECONDS:.0f}s bound"
            )
    if stderr:
        print(stderr, end="" if stderr.endswith("\n") else "\n")
    if process.returncode != 0:
        raise ReproductionError(f"lean4export exited {process.returncode}")
    print(
        f"exported {output_path.stat().st_size:,} bytes in "
        f"{time.monotonic() - started:.1f}s"
    )


def extend_name_table(record: object, names: dict[int, str]) -> None:
    if not isinstance(record, dict) or "in" not in record:
        return
    name_id = record["in"]
    if not isinstance(name_id, int):
        raise ReproductionError("invalid lean4export name-table identifier")
    if "str" in record:
        node = record["str"]
        segment_key = "str"
    elif "num" in record:
        node = record["num"]
        segment_key = "i"
    else:
        raise ReproductionError("unknown lean4export name-table record")
    if not isinstance(node, dict):
        raise ReproductionError("invalid lean4export name-table node")
    prefix_id = node.get("pre")
    segment = node.get(segment_key)
    if not isinstance(prefix_id, int) or prefix_id not in names:
        raise ReproductionError("unresolved lean4export name-table prefix")
    if segment_key == "str" and not isinstance(segment, str):
        raise ReproductionError("invalid lean4export string-name segment")
    if segment_key == "i" and not isinstance(segment, int):
        raise ReproductionError("invalid lean4export numeric-name segment")
    prefix = names[prefix_id]
    rendered_segment = str(segment)
    names[name_id] = (
        f"{prefix}.{rendered_segment}" if prefix else rendered_segment
    )


def resolved_theorem_name(record: object, names: dict[int, str]) -> str:
    if not isinstance(record, dict) or not isinstance(record.get("thm"), dict):
        raise ReproductionError("invalid lean4export theorem record")
    name_id = record["thm"].get("name")
    if not isinstance(name_id, int) or name_id not in names:
        raise ReproductionError("unresolved lean4export theorem name")
    return names[name_id]


def compare_followup_exports(base_path: Path, target_path: Path) -> None:
    differing_records = 0
    binder_changes = 0
    name_changes = 0
    line_count = 0
    pending_differences = 0
    wrapper_owner_count = 0
    concrete_theorem_count = 0
    base_names = {0: ""}
    target_names = {0: ""}

    with (
        base_path.open(encoding="utf-8") as base_stream,
        target_path.open(encoding="utf-8") as target_stream,
    ):
        while True:
            base_line = base_stream.readline()
            target_line = target_stream.readline()
            if not base_line and not target_line:
                break
            line_count += 1
            if not base_line or not target_line:
                raise ReproductionError("follow-up exports have different line counts")
            if base_line == target_line:
                if base_line.startswith('{"in":'):
                    record = json.loads(base_line)
                    extend_name_table(record, base_names)
                    extend_name_table(record, target_names)
                elif base_line.startswith(DECLARATION_PREFIXES):
                    base_record = json.loads(base_line)
                    target_record = json.loads(target_line)
                    base_theorem = None
                    if "thm" in base_record:
                        base_theorem = resolved_theorem_name(
                            base_record, base_names
                        )
                        target_theorem = resolved_theorem_name(
                            target_record, target_names
                        )
                        if base_theorem != target_theorem:
                            raise ReproductionError(
                                "equal theorem records resolved to different names"
                            )
                    if pending_differences:
                        if base_theorem is None:
                            declaration_kind = next(iter(base_record))
                            raise ReproductionError(
                                "export differences crossed an unexpected "
                                f"{declaration_kind} declaration boundary"
                            )
                        if base_theorem != WRAPPER_DECLARATION:
                            raise ReproductionError(
                                "export differences belong to unexpected theorem "
                                f"{base_theorem}"
                            )
                        wrapper_owner_count += 1
                        pending_differences = 0
                    if base_theorem == EXPORT_THEOREM:
                        concrete_theorem_count += 1
                continue

            differing_records += 1
            base_record = json.loads(base_line)
            target_record = json.loads(target_line)
            kinds = [kind for kind in ("forallE", "lam") if kind in base_record]
            if len(kinds) != 1 or kinds[0] not in target_record:
                raise ReproductionError(
                    f"unexpected export difference at line {line_count}"
                )
            kind = kinds[0]
            base_node = dict(base_record[kind])
            target_node = dict(target_record[kind])

            base_name = base_node.pop("name")
            target_name = target_node.pop("name")
            base_binder = base_node.pop("binderInfo")
            target_binder = target_node.pop("binderInfo")
            if base_name not in base_names or target_name not in target_names:
                raise ReproductionError(
                    f"unresolved changed binder name at line {line_count}"
                )
            base_name_text = base_names[base_name]
            target_name_text = target_names[target_name]
            changed_name = base_name != target_name
            changed_binder = base_binder != target_binder
            if changed_name == changed_binder:
                raise ReproductionError(
                    f"expected exactly one binder field change at line {line_count}"
                )
            if changed_name:
                if (base_name_text, target_name_text) != ("hq", "hcard"):
                    raise ReproductionError(
                        f"unexpected binder rename at line {line_count}: "
                        f"{base_name_text} -> {target_name_text}"
                    )
                name_changes += 1
            if changed_binder:
                if (
                    base_name_text != "q"
                    or target_name_text != "q"
                    or (base_binder, target_binder) != ("default", "implicit")
                ):
                    raise ReproductionError(
                        f"unexpected binder transition at line {line_count}"
                    )
                binder_changes += 1

            base_record = {**base_record, kind: base_node}
            target_record = {**target_record, kind: target_node}
            if base_record != target_record:
                raise ReproductionError(
                    f"non-binder export difference at line {line_count}"
                )
            pending_differences += 1

    if pending_differences:
        raise ReproductionError("unowned export differences at end of stream")

    if (differing_records, binder_changes, name_changes) != (5, 3, 2):
        raise ReproductionError(
            "unexpected follow-up export delta: "
            f"{differing_records} records, {binder_changes} binder changes, "
            f"{name_changes} name changes"
        )
    if wrapper_owner_count != 1:
        raise ReproductionError(
            "follow-up export differences were not owned by exactly one "
            f"{WRAPPER_DECLARATION} theorem record"
        )
    if concrete_theorem_count != 1:
        raise ReproductionError(
            f"expected one unchanged {EXPORT_THEOREM} theorem record"
        )
    byte_delta = target_path.stat().st_size - base_path.stat().st_size
    if byte_delta != 7:
        raise ReproductionError(
            f"unexpected follow-up export size delta: {byte_delta:+d} bytes"
        )

    print("\n=== Export-shape control ===")
    print(f"both closures: {line_count:,} NDJSON records")
    print(
        f"only five {WRAPPER_DECLARATION} expression records differ: "
        "three q binder-info changes and two hq-to-hcard binder renames"
    )
    print(
        f"base bytes: {base_path.stat().st_size:,}; "
        f"target bytes: {target_path.stat().st_size:,} "
        f"({byte_delta:+d} bytes)"
    )
    print(f"unchanged concrete theorem record: {EXPORT_THEOREM}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("target", "both"),
        default="both",
        help="run only the target, or the target followed by its bounded base",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="seconds allowed for each Comparator run",
    )
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.timeout > 90:
        parser.error("--timeout must be positive and at most 90 seconds")
    return args


def main() -> int:
    args = parse_args()
    if BASE_EXPECTATION not in {"accepted", "pathological"}:
        raise ReproductionError(
            f"unknown base expectation {BASE_EXPECTATION!r}; "
            "expected 'accepted' or 'pathological'"
        )
    if COMPARE_EXPORTS and BASE_EXPECTATION != "accepted":
        raise ReproductionError(
            "export-shape comparison is only defined for the accepting follow-up pair"
        )
    memory_limit = cgroup_memory_limit()
    assert_runtime_limits(memory_limit)
    print(f"CompPoly Comparator replay reproduction: {SUITE_NAME} suite")
    print(f"host architecture: {platform.machine()}")
    print(f"cgroup memory cap: {memory_limit / 1024**3:.1f} GiB")
    print(f"Lean:              {LEAN_VERSION}")
    print(f"Comparator:        {COMPARATOR_REV}")
    print(f"lean4export:       {LEAN4EXPORT_REV}")
    print(f"target CompPoly:   {TARGET_REV}")
    print(f"base CompPoly:     {BASE_REV}")
    print(f"base expectation:  {BASE_EXPECTATION}")
    assert_pins()

    print("\n=== Target revision (run first to validate the setup) ===")
    target_build = prepare_revision(TARGET_REV)
    target = run_comparator(args.timeout)
    require_acceptance("target", target)

    if args.mode == "target":
        print(
            f"\nPASS: target revision built in {target_build:.1f}s and completed "
            f"the full Comparator pipeline in {target.elapsed_seconds:.1f}s"
        )
        return 0

    target_export = Path("/tmp/target-sextic.ndjson")
    base_export = Path("/tmp/base-sextic.ndjson")
    if COMPARE_EXPORTS:
        export_closure(target_export)

    print("\n=== Base revision (hard-bounded by time and container memory) ===")
    base_build = prepare_revision(BASE_REV)
    base = run_comparator(args.timeout)
    if BASE_EXPECTATION == "pathological":
        classification = classify_pathological_base(base)
        base_result = f"{classification} after {base.elapsed_seconds:.1f}s"
    else:
        require_acceptance("base", base)
        base_result = f"full pipeline accepted in {base.elapsed_seconds:.1f}s"

    if COMPARE_EXPORTS:
        export_closure(base_export)
        compare_followup_exports(base_export, target_export)

    print("\n=== Result ===")
    print(
        f"target {TARGET_REV[:7]}: normal build {target_build:.1f}s; "
        f"full pipeline accepted in {target.elapsed_seconds:.1f}s"
    )
    print(
        f"base   {BASE_REV[:7]}: normal build {base_build:.1f}s; {base_result}"
    )
    if BASE_EXPECTATION == "pathological":
        print("\nPASS: reproduced the old-versus-wrapper Comparator behavior")
    else:
        print("\nPASS: both revisions completed the same bounded Comparator check")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ReproductionError) as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
