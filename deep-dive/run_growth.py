#!/usr/bin/env python3
"""Bypass Lean's stack guard and observe the exact old replay grow under a cap."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import math
import os
from pathlib import Path
import resource
import signal
import subprocess
import tempfile
import time

from run_mechanism import (
    COMPARATOR_ROOT,
    DIAGNOSTIC,
    INSTRUMENTED_LEAN,
    LEAN_SOURCE,
    ExperimentError,
    assert_pins,
    display,
    export_target,
    prepare_old_revision,
)


GIB = 1024**3
MAX_LOG_BYTES = 64 * 1024**2
SAMPLE_SECONDS = 0.25
REPORT_SECONDS = 2.0
START_MARKER = "THEOREM_PHASE check-body"


@dataclass(frozen=True)
class GrowthProfile:
    name: str
    container_memory_gib: int
    stop_memory_gib: int
    timeout_seconds: float
    minimum_growth_gib: int
    minimum_total_gib: int
    minimum_available_gib: int

    @property
    def container_memory_bytes(self) -> int:
        return self.container_memory_gib * GIB

    @property
    def stop_memory_bytes(self) -> int:
        return self.stop_memory_gib * GIB


PROFILES = {
    "bounded": GrowthProfile("bounded", 8, 6, 120.0, 2, 0, 0),
    "large-40": GrowthProfile("large-40", 48, 40, 600.0, 30, 56, 44),
}


class GrowthError(ExperimentError):
    pass


def read_ascii(path: Path) -> str:
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError as error:
        raise GrowthError(f"cannot read {path}: {error}") from error


def cgroup_v2_paths() -> tuple[Path, Path, Path]:
    root = Path("/sys/fs/cgroup")
    limit = root / "memory.max"
    current = root / "memory.current"
    events = root / "memory.events"
    if not (limit.is_file() and current.is_file() and events.is_file()):
        raise GrowthError("growth mode requires a cgroup-v2 Docker runtime")
    return limit, current, events


def memory_events(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in read_ascii(path).splitlines():
        fields = line.split()
        if len(fields) != 2:
            raise GrowthError(f"invalid cgroup memory event: {line!r}")
        result[fields[0]] = int(fields[1])
    if "oom" not in result or "oom_kill" not in result:
        raise GrowthError("cgroup memory.events has no oom/oom_kill counters")
    return result


def meminfo_bytes(field: str) -> int:
    try:
        lines = Path("/proc/meminfo").read_text(encoding="ascii").splitlines()
    except OSError as error:
        raise GrowthError(f"cannot read /proc/meminfo: {error}") from error
    matches = [line for line in lines if line.startswith(f"{field}:")]
    if len(matches) != 1:
        raise GrowthError(f"/proc/meminfo has no unique {field} value")
    fields = matches[0].split()
    if len(fields) != 3 or fields[2] != "kB" or not fields[1].isdigit():
        raise GrowthError(f"invalid {field} line: {matches[0]!r}")
    return int(fields[1]) * 1024


def assert_profile_host_memory(profile: GrowthProfile) -> None:
    if profile.minimum_total_gib:
        total = meminfo_bytes("MemTotal")
        minimum_total = profile.minimum_total_gib * GIB
        if total < minimum_total:
            raise GrowthError(
                f"large profile requires at least {profile.minimum_total_gib} GiB "
                f"visible to the container; /proc/meminfo reports {total / GIB:.3f} GiB"
            )
    if profile.minimum_available_gib:
        available = meminfo_bytes("MemAvailable")
        minimum = profile.minimum_available_gib * GIB
        if available < minimum:
            raise GrowthError(
                f"large profile requires at least {profile.minimum_available_gib} GiB "
                f"currently available; /proc/meminfo reports {available / GIB:.3f} GiB"
            )


def assert_growth_limits(
    profile: GrowthProfile,
) -> tuple[Path, Path, dict[str, int]]:
    limit_path, current_path, events_path = cgroup_v2_paths()
    raw_limit = read_ascii(limit_path)
    if raw_limit == "max":
        raise GrowthError("container has no memory cap")
    try:
        limit = int(raw_limit)
    except ValueError as error:
        raise GrowthError(f"invalid cgroup memory limit {raw_limit!r}") from error
    if limit != profile.container_memory_bytes:
        raise GrowthError(
            f"container memory cap is {limit / GIB:.3f} GiB; expected exactly "
            f"{profile.container_memory_gib} GiB"
        )

    swap_path = Path("/sys/fs/cgroup/memory.swap.max")
    if not swap_path.is_file() or read_ascii(swap_path) != "0":
        raise GrowthError("container must use cgroup v2 with swap disabled")

    soft, hard = resource.getrlimit(resource.RLIMIT_STACK)
    if soft != resource.RLIM_INFINITY or hard != resource.RLIM_INFINITY:
        raise GrowthError(
            "growth mode requires an unlimited OS main-thread stack; "
            "use the supplied growth.sh"
        )
    if profile.stop_memory_bytes >= limit - GIB:
        raise GrowthError("userspace stop must leave at least 1 GiB below the cgroup cap")
    assert_profile_host_memory(profile)
    initial_events = memory_events(events_path)
    for event in ("oom", "oom_kill"):
        if initial_events[event] != 0:
            raise GrowthError(f"fresh experiment cgroup starts with {event} events")
    return current_path, events_path, initial_events


def assert_guard_bypass_source() -> None:
    thread_source = (LEAN_SOURCE / "src/runtime/thread.cpp").read_text(
        encoding="utf-8"
    )
    stack_source = (LEAN_SOURCE / "src/runtime/stackinfo.cpp").read_text(
        encoding="utf-8"
    )
    main_thread_branch = '''if (use_thread_env && std::strcmp(use_thread_env, "0") == 0) {
        return main_fn(argc, argv);
    }'''
    overflow_guard = '''if (g_stack_threshold > g_stack_base + LEAN_STACK_BUFFER_SPACE) {
        // negative overflow
        g_stack_threshold = 0;
    }'''
    if main_thread_branch not in thread_source:
        raise GrowthError("pinned Lean main-thread bypass source drift")
    if overflow_guard not in stack_source:
        raise GrowthError("pinned Lean stack-threshold overflow behavior drift")


def kill_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def cgroup_bytes(path: Path) -> int:
    try:
        return int(read_ascii(path))
    except ValueError as error:
        raise GrowthError("invalid cgroup memory.current value") from error


def regression(samples: list[tuple[float, int]]) -> tuple[float, float]:
    xs = [elapsed for elapsed, _ in samples]
    ys = [memory / GIB for _, memory in samples]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        raise GrowthError("not enough elapsed time to measure growth")
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
    intercept = mean_y - slope * mean_x
    residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
    total = sum((y - mean_y) ** 2 for y in ys)
    r_squared = 1.0 - residual / total if total else math.nan
    return slope, r_squared


def run_growth(
    export_path: Path,
    current_path: Path,
    events_path: Path,
    before_events: dict[str, int],
    profile: GrowthProfile,
) -> None:
    command = [
        "lake",
        "env",
        INSTRUMENTED_LEAN,
        "--run",
        DIAGNOSTIC,
        export_path,
    ]
    print(f"\n$ {display(command)}", flush=True)
    print("  internal guard: BYPASSED (unlimited OS stack + LEAN_MAIN_USE_THREAD=0)")
    print(
        f"  external safety: stop at {profile.stop_memory_gib} GiB; "
        f"Docker hard cap {profile.container_memory_gib} GiB; swap disabled"
    )
    print("\n  elapsed | cgroup memory.current | phase", flush=True)

    child_env = {
        **os.environ,
        "LEAN_NUM_THREADS": "1",
        "LEAN_MAIN_USE_THREAD": "0",
    }
    child_env.pop("LEAN_STACK_SIZE_KB", None)

    def limit_log() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_LOG_BYTES, MAX_LOG_BYTES))

    log_fd, log_name = tempfile.mkstemp(prefix="comppoly-growth-", suffix=".log")
    try:
        with os.fdopen(log_fd, "wb", buffering=0) as log:
            process = subprocess.Popen(
                [str(arg) for arg in command],
                cwd=COMPARATOR_ROOT,
                env=child_env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                preexec_fn=limit_log,
            )
            started = time.monotonic()
            samples: list[tuple[float, int]] = []
            replay_samples: list[tuple[float, int]] = []
            peak = 0
            replay_peak = 0
            marker_seen = False
            marker_memory = 0
            next_report = 0.0
            stopped_by_watchdog = False
            timed_out = False
            output_seen = bytearray()
            log_offset = 0

            try:
                while process.poll() is None:
                    elapsed = time.monotonic() - started
                    current = cgroup_bytes(current_path)
                    peak = max(peak, current)
                    samples.append((elapsed, current))
                    size = os.fstat(log.fileno()).st_size
                    if size > MAX_LOG_BYTES:
                        raise GrowthError("instrumented replay exceeded its log-size bound")
                    if size > log_offset:
                        output_seen.extend(
                            os.pread(log.fileno(), size - log_offset, log_offset)
                        )
                        log_offset = size
                    if not marker_seen and START_MARKER.encode() in output_seen:
                        marker_seen = True
                        marker_memory = current
                        print(
                            f"  {elapsed:7.1f}s | {current / GIB:8.3f} GiB | target replay",
                            flush=True,
                        )
                        next_report = elapsed + REPORT_SECONDS
                    if marker_seen:
                        replay_samples.append((elapsed, current))
                        replay_peak = max(replay_peak, current)
                        if elapsed >= next_report:
                            print(
                                f"  {elapsed:7.1f}s | {current / GIB:8.3f} GiB | growing",
                                flush=True,
                            )
                            next_report += REPORT_SECONDS
                    if current >= profile.stop_memory_bytes:
                        stopped_by_watchdog = True
                        kill_group(process)
                        break
                    if elapsed >= profile.timeout_seconds:
                        timed_out = True
                        kill_group(process)
                        break
                    time.sleep(SAMPLE_SECONDS)
                process.wait(timeout=10.0)
            finally:
                if process.poll() is None:
                    kill_group(process)
                    process.wait(timeout=10.0)
            final_size = os.fstat(log.fileno()).st_size
            if final_size > MAX_LOG_BYTES:
                raise GrowthError("instrumented replay exceeded its log-size bound")
            output = os.pread(log.fileno(), final_size, 0).decode("utf-8", "replace")
    finally:
        try:
            os.unlink(log_name)
        except FileNotFoundError:
            pass

    after_events = memory_events(events_path)
    for event in ("oom", "oom_kill"):
        if after_events.get(event) != before_events.get(event):
            raise GrowthError(f"Docker cgroup {event} counter changed before the userspace stop")
    if timed_out:
        raise GrowthError("growth replay reached its wall timeout before the memory stop")
    if not stopped_by_watchdog:
        raise GrowthError(f"growth replay exited early with status {process.returncode}")
    if process.returncode != -signal.SIGKILL:
        raise GrowthError(
            f"watchdog stop produced status {process.returncode}, expected SIGKILL"
        )
    if not marker_seen or output.count(START_MARKER) != 1:
        raise GrowthError("exact target body-check marker was not observed once")
    for forbidden in (
        "target accepted",
        "target failed",
        "GROWTH_REPLAY_OK",
        "THEOREM_PHASE final-defeq",
        "THEOREM_PHASE checked",
        "STACK_COMPONENT",
        "deep recursion detected",
        "segmentation fault",
    ):
        if forbidden.lower() in output.lower():
            raise GrowthError(f"unexpected terminal marker before watchdog: {forbidden}")
    for required in (
        "replaying dependencies",
        "dependencies accepted",
        "replaying target KoalaBear.sexticPoly_irreducible",
        "THEOREM_PHASE begin",
        "THEOREM_PHASE check-body",
    ):
        if required not in output:
            raise GrowthError(f"missing exact replay marker: {required}")
    if peak < profile.stop_memory_bytes:
        raise GrowthError("memory watchdog fired below its configured threshold")
    if replay_peak < profile.stop_memory_bytes:
        raise GrowthError("target-scoped memory never reached the watchdog threshold")
    growth = replay_peak - marker_memory
    if growth < profile.minimum_growth_gib * GIB:
        raise GrowthError(
            f"target replay grew only {growth / GIB:.3f} GiB; expected at least "
            f"{profile.minimum_growth_gib} GiB for profile {profile.name}"
        )
    if len(replay_samples) < 8:
        raise GrowthError("too few target-replay samples to characterize growth")
    slope, r_squared = regression(replay_samples)
    if not math.isfinite(slope) or slope <= 0.02:
        raise GrowthError(f"target replay did not show positive memory growth: {slope}")

    print(
        f"  {samples[-1][0]:7.1f}s | {replay_peak / GIB:8.3f} GiB | watchdog stop",
        flush=True,
    )
    print("\nBOUNDED GUARD-BYPASS GROWTH REPRODUCED")
    print(f"  profile:             {profile.name}")
    print(f"  target-start memory: {marker_memory / GIB:.3f} GiB")
    print(f"  whole-cgroup peak after target start: {replay_peak / GIB:.3f} GiB")
    print(f"  increase after target start:          {growth / GIB:.3f} GiB")
    print(f"  linear fit:         {slope:.3f} GiB/s (R²={r_squared:.3f})")
    print("  termination:        userspace watchdog, before cgroup OOM")
    print(
        f"  outcome:            reached {profile.stop_memory_gib} GiB "
        "whole-container memory.current"
    )
    print("PASS: bypassing Lean's guard lets the exact old target continue growing")


def parse_args() -> GrowthProfile:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=tuple(PROFILES), default="bounded")
    return PROFILES[parser.parse_args().profile]


def main() -> int:
    profile = parse_args()
    current_path, events_path, before_events = assert_growth_limits(profile)
    assert_pins()
    assert_guard_bypass_source()
    prepare_old_revision()
    export_path = Path("/tmp/comppoly-old-sextic-growth.ndjson")
    export_target(export_path)
    assert_profile_host_memory(profile)
    run_growth(export_path, current_path, events_path, before_events, profile)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ExperimentError as error:
        raise SystemExit(f"growth experiment failed closed: {error}")
