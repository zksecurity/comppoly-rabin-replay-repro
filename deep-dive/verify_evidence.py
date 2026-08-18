#!/usr/bin/env python3
"""Fail closed if the committed deep-replay evidence does not support its claims."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parent
EVIDENCE = ROOT / "evidence"
PATCH = ROOT / "patches" / "lean-4.32.2-kernel-probe.patch"
TRACE_LAST_PATCH = ROOT / "fixtures" / "pos-trace-last.patch"
COP2_FIRST_PATCH = ROOT / "fixtures" / "pos-cop2-first.patch"
RUNS = EVIDENCE / "runs"
LEAN_COMMIT = "f3b06c705e6c85f5314019d5d3baab0fec5b580c"

EXPECTED_HASHES = {
    PATCH: "25a72ee897a26b37ee9df3150427405457f910c069b658a5875596389cec57be",
    TRACE_LAST_PATCH: "591f6f7e3be9dec64179e522c8113c441b72da1dafef1082d5891bfd1805868f",
    COP2_FIRST_PATCH: "d53227d654a9c98233b131e91c47ddda14f81e56d17426af2bbb2b7c5834979f",
    ROOT / "fixtures" / "TransportVariants.lean":
        "6b6e8dd80974e56a576f808e1343d1b67f361994f78480d9b82706a84c575884",
    ROOT / "probes" / "DiagnosticReplay.lean":
        "b6ed19b74851847cd68e85a541384e0b58e1ffbc276e04bd2f1450d28552c870",
    ROOT / "probes" / "SubtermCheck.lean":
        "853d4e5c357b5c0d242c819600e3cea4fd8ee3568e8e66fc96b7cec972eaa706",
    ROOT / "probes" / "CompareTransport.lean":
        "09e4fa9c7608bf76e11c843e1d2251c99ed18f1991df0930e96c1bf4de8eb02c",
    ROOT / "probes" / "analyze_export_graph.py":
        "1fe3585595911b0e1b8493e0430739476843d83809f5b9e1a18560d294eb8bf0",
    ROOT / "probes" / "ArchivedDirectReplay.lean":
        "b42aa71c07b820311493636ef7454e53ddc3d99a96896b3330356c8f3d91a1c4",
    EVIDENCE / "summary.json":
        "729ceebf58e78d3d390ff6939551856bb41f38e241cec9ab06e229dc9d6eb0ec",
    EVIDENCE / "old-first-transport.txt":
        "b837c74c052b3fe91a6dfd4554763b52138d091eb24c49f9f829dcf62e7d8df4",
    EVIDENCE / "trace-last-baseline.txt":
        "92b4970635b0e997f50b6b057dbdcdb9d446d251ba1ce404f5bf6cd27df5026b",
    EVIDENCE / "trace-last-reset-eqv.txt":
        "96142e8fe8232838a6422a4a4d98114d38698e6f99b6add3a0827290d34922c5",
    EVIDENCE / "trace-last-no-eqv-add.txt":
        "42c7375221667824923c20a682b1a1f0ad8ef6d176dededb10e52d56915e8d25",
    EVIDENCE / "transport-structural-equality.json":
        "e66e59780e92f088ae27925f15c5e30ab9f741295d8ef66a178a0725bcbd4cad",
    EVIDENCE / "archived-guard-bypass-58g.json":
        "b3df60951a48a51f4bdfee5015c2985b515ad0002edccf65fce26939554ca3e0",
    EVIDENCE / "archived-guard-bypass-58g.txt":
        "c2ce956ff4ea99246c6e33f42b565891b1719b966b8f84cb53fa928584beb32a",
    EVIDENCE / "manifest.json":
        "da886fc5145dca6f24c4b7be209aacdfa21655b4c8715fc9d435fc17e46e2301",
    RUNS / "results.json":
        "61f02f3f3f8fa4755edaf9268d86a8072009df103d46fb749a810770afad4e8b",
    RUNS / "replays-8g.json":
        "81a0e191216f25ef88dde688bbd11269b544bef3c170e13a35b60fc49baf93ab",
    RUNS / "subsets-extra.json":
        "07582b1f995dc356182f2e2eb650e3035c72452176dce35f4206511584aec928",
    RUNS / "position-controls.json":
        "16082e564e85356a145373bea05fb2ec28f38545662981c2e1fb0c0cc4b24275",
    RUNS / "subterm-checks.json":
        "52d08912dc3b2a71ed17fe46017eb52b771ee01684ba187f95b7ccf783321b80",
    RUNS / "graph-audits.json":
        "1e48111cee29d0e041597ebfd6871c3879a8ce3a67630fac1c7c14093e50e470",
    RUNS / "diagnostics.json":
        "c5b184a0341be863365638d58b5b26d82abfe593ff83db7d6d84644beb62857c",
    RUNS / "cache-probes.json":
        "6dd8d665d163d3f465a7065b7dfef9e06b44c9eeca3e8b3dbc1875079b275401",
}


class EvidenceError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text())


def scalar_counter(text: str, name: str) -> int:
    matches = re.findall(rf"^{re.escape(name)} (\d+)$", text, re.MULTILINE)
    require(len(matches) == 1, f"expected one {name} counter, found {len(matches)}")
    return int(matches[0])


def unfold_counts(text: str) -> dict[str, int]:
    return {
        name: int(count)
        for count, name in re.findall(r"^FAILED_UNFOLD (\d+) (.+)$", text, re.MULTILINE)
    }


def watched_unfold_counts(text: str) -> dict[str, int]:
    return {
        name: int(count)
        for name, count in re.findall(
            r"^FAILED_UNFOLD_WATCH (.+) (\d+)$", text, re.MULTILINE
        )
    }


def verify_archived_guard_bypass() -> None:
    record = load_json(EVIDENCE / "archived-guard-bypass-58g.json")
    transcript_path = EVIDENCE / "archived-guard-bypass-58g.txt"
    source_path = ROOT / "probes" / "ArchivedDirectReplay.lean"
    transcript = transcript_path.read_text(encoding="utf-8")

    require(record["schema_version"] == 1, "guard-bypass schema drift")
    require(
        record["record_kind"] == "archived_resource_observation",
        "guard-bypass record kind drift",
    )
    require(
        record["status"] == "archived_not_run_by_default",
        "guard-bypass status drift",
    )
    program = record["program"]
    require(program["kind"] == "direct_lean_environment_replay", "wrong replay kind")
    require(program["calls"] == "Lean.Environment.replay", "wrong replay operation")
    require(not program["uses_comparator"], "archived run incorrectly claims Comparator")
    require(not program["uses_lean4export"], "archived run incorrectly claims lean4export")
    require(program["source_bytes"] == source_path.stat().st_size == 868, "source size drift")
    require(program["source_sha256"] == sha256(source_path), "source hash drift")

    runtime = record["runtime"]
    require(runtime["network"] == "none", "archived run allowed networking")
    require(runtime["memory_limit_bytes"] == 58 * 1024**3, "wrong memory cap")
    require(
        runtime["memory_swap_limit_bytes"] == runtime["memory_limit_bytes"]
        and not runtime["swap_beyond_memory_limit"],
        "archived run allowed swap beyond its memory cap",
    )
    require(runtime["stack_rlimit"] == "unlimited", "stack guard was not bypassed")
    require(
        runtime["guard_relevant_environment"] == {"LEAN_MAIN_USE_THREAD": "0"},
        "main-thread bypass environment drift",
    )

    lines = transcript.splitlines()
    require(lines[0] == "t(s)  mem(GiB)", "growth transcript header drift")
    require(
        lines[-1] == "final status=exited exit=0 oom=true",
        "growth transcript outcome drift",
    )
    transcript_samples: list[tuple[int, float]] = []
    for line in lines[1:-2]:
        match = re.fullmatch(r"(\d+)\s+([0-9]+\.[0-9])", line)
        require(match is not None, f"invalid growth transcript row: {line!r}")
        transcript_samples.append((int(match.group(1)), float(match.group(2))))
    require(lines[-2] == "280   0.0", "terminal growth poll drift")

    measurement = record["measurement"]
    require(measurement["metric"] == "cgroup_v2.memory.current", "wrong memory metric")
    require(measurement["scope"] == "whole_container", "wrong memory scope")
    require(not measurement["is_process_rss"], "cgroup memory mislabeled as RSS")
    require(measurement["source_precision_gib"] == 0.1, "source precision drift")
    require(
        measurement["normalized_transcript_sha256"] == sha256(transcript_path),
        "normalized transcript hash drift",
    )
    json_samples = [
        (sample["nominal_elapsed_seconds"], sample["memory_current_gib"])
        for sample in measurement["samples"]
    ]
    require(transcript_samples == json_samples, "JSON and transcript samples differ")
    require(len(json_samples) == 14, "wrong number of valid growth samples")
    require(
        [seconds for seconds, _ in json_samples] == list(range(0, 261, 20)),
        "growth sample spacing drift",
    )
    values = [value for _, value in json_samples]
    require(all(left < right for left, right in zip(values, values[1:])), "memory did not grow")
    require(values[-1] == 56.0, "wrong last valid memory sample")
    terminal = measurement["terminal_poll"]
    require(
        terminal["nominal_elapsed_seconds"] == 280
        and terminal["reported_gib"] == 0.0
        and not terminal["is_memory_sample"],
        "terminal poll semantics drift",
    )

    xs = [float(seconds) for seconds, _ in json_samples]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(values) / len(values)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values)) / denominator
    intercept = mean_y - slope * mean_x
    residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, values))
    total = sum((y - mean_y) ** 2 for y in values)
    r_squared = 1.0 - residual / total
    reported = record["derived"]["ordinary_least_squares_over_all_14_valid_samples"]
    require(math.isclose(slope, reported["slope_gib_per_second"], abs_tol=1e-10), "slope drift")
    require(math.isclose(intercept, reported["intercept_gib"], abs_tol=1e-10), "intercept drift")
    require(math.isclose(r_squared, reported["r_squared"], abs_tol=1e-10), "R-squared drift")
    require(slope > 0.2 and r_squared > 0.99, "archived growth was not approximately linear")

    outcome = record["outcome"]
    require(outcome["docker_oom_killed"], "archived run was not OOM-killed")
    require(outcome["exit_code_masked_by_shell_pipeline"], "masked exit-code caveat missing")
    require(not outcome["completion_marker_observed"], "archived replay unexpectedly completed")
    require(outcome["classification"] == "cgroup_oom_unfinished", "wrong outcome")
    require(
        outcome["last_valid_sample_gib"] == values[-1],
        "outcome does not match the last valid sample",
    )
    require(not outcome["exact_peak_available"], "record invents an exact peak")

    control = record["companion_controls"]["instrumented_exact_old_revision"]
    require(
        control["source_revision"]
        == "6133f9f796707c438d0a614f97dc218ae976ab8f",
        "archived companion source revision drift",
    )
    require(
        control["export_sha256"]
        == "68378a3bdd5976651c248d4d7d404b82bebfd5fa5ec70a5fde5dc95702850ebb",
        "archived companion export hash drift",
    )
    require(
        control["diagnostic_log_sha256"]
        == EXPECTED_HASHES[EVIDENCE / "old-first-transport.txt"],
        "archived companion log hash drift",
    )
    require(control["npow_rec_successor_unfolds_observed_before_guard"] == 5778, "npow count drift")
    require(
        all(count == 0 for count in control["watched_cardinality_unfolds"].values()),
        "archived companion reports cardinality unfolding",
    )


def verify_lean_checkout(path: Path) -> None:
    head = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True
    ).strip()
    require(head == LEAN_COMMIT, f"Lean HEAD {head} != pinned {LEAN_COMMIT}")
    forward = subprocess.run(
        ["git", "apply", "--check", str(PATCH)], cwd=path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    reverse = subprocess.run(
        ["git", "apply", "--check", "--reverse", str(PATCH)], cwd=path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    tracked_status = subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=path, text=True
    )
    if forward.returncode == 0:
        require(not tracked_status, "unpatched Lean checkout has unrelated tracked changes")
    elif reverse.returncode == 0:
        actual_diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=path)
        require(actual_diff == PATCH.read_bytes(), "applied checkout differs from exact probe patch")
    else:
        raise EvidenceError("kernel probe patch is neither cleanly applicable nor already applied")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lean-src", type=Path,
        help="optionally verify the probe patch against an exact Lean checkout",
    )
    args = parser.parse_args()

    for path, expected in EXPECTED_HASHES.items():
        actual = sha256(path)
        require(actual == expected, f"SHA-256 mismatch for {path}: {actual}")

    verify_archived_guard_bypass()

    summary = load_json(EVIDENCE / "summary.json")
    manifest = load_json(EVIDENCE / "manifest.json")
    manifest_sources = {
        "kernel_patch_sha256": PATCH,
        "trace_last_patch_sha256": TRACE_LAST_PATCH,
        "cop2_first_patch_sha256": COP2_FIRST_PATCH,
        "transport_variants_sha256": ROOT / "fixtures" / "TransportVariants.lean",
        "diagnostic_replay_sha256": ROOT / "probes" / "DiagnosticReplay.lean",
        "subterm_check_sha256": ROOT / "probes" / "SubtermCheck.lean",
        "compare_transport_sha256": ROOT / "probes" / "CompareTransport.lean",
        "analyze_export_graph_sha256": ROOT / "probes" / "analyze_export_graph.py",
    }
    require(
        set(manifest["source_artifacts"]) == set(manifest_sources),
        "manifest source-artifact inventory drift",
    )
    for name, path in manifest_sources.items():
        require(
            manifest["source_artifacts"][name] == EXPECTED_HASHES[path],
            f"manifest source hash drift for {name}",
        )
    pins = summary["pins"]
    require(pins["lean_commit"] == LEAN_COMMIT, "wrong Lean source pin")
    require(
        pins["comppoly"] == "641694629e4557520a1539b272ec338c9f3044c7",
        "wrong CompPoly source pin",
    )
    require(
        manifest["pins"]["comppoly_old_first"]
        == "6133f9f796707c438d0a614f97dc218ae976ab8f",
        "wrong old-first CompPoly source pin",
    )
    require(manifest["pins"]["lean_commit"] == LEAN_COMMIT, "manifest Lean pin drift")
    require(
        manifest["exports"]["trace_last_target"]["sha256"]
        == "3230ba9e3c41a1b1a01617c5be60b69ad4e3cff3460e7d4cd4c57de89ab78175",
        "trace-last target export hash drift",
    )
    require(
        manifest["exports"]["trace_last_target"]["diagnostic_target"]
        == "KoalaBear.sexticPoly_irreducible",
        "trace-last diagnostic target drift",
    )
    for export_name in ("trace_last_target", "old_first_target"):
        export = manifest["exports"][export_name]
        require(
            export["command"][-3] == export["module"]
            and export["command"][-1] == export["declaration"],
            f"{export_name} command does not match its module/declaration",
        )
    old_first = manifest["old_first_run"]
    require(
        manifest["exports"]["old_first_target"]["sha256"]
        == old_first["export_sha256"]
        == "68378a3bdd5976651c248d4d7d404b82bebfd5fa5ec70a5fde5dc95702850ebb",
        "old-first target export hash drift",
    )
    require(
        old_first["source_revision"] == manifest["pins"]["comppoly_old_first"],
        "old-first source revision drift",
    )
    require(
        old_first["log_sha256"]
        == EXPECTED_HASHES[EVIDENCE / "old-first-transport.txt"],
        "old-first log ownership drift",
    )
    require(old_first["outcome"] == "deep_recursion", "old-first outcome drift")
    for name, run in manifest["causal_runs"].items():
        if "log_sha256" in run:
            require(
                run["log_sha256"] in EXPECTED_HASHES.values(),
                f"manifest {name} log is not committed",
            )

    factorial = summary["factorial_original_condition_order"]
    replay_runs = load_json(RUNS / "replays-8g.json")["variants"]
    extra_runs = load_json(RUNS / "subsets-extra.json")["variants"]
    factorial_sources = {
        "none": replay_runs["k0"],
        "cop2": replay_runs["k1"],
        "cop3": extra_runs["cop3"],
        "trace": extra_runs["trace"],
        "cop3+cop2": replay_runs["k2"],
        "trace+cop3": extra_runs["trace_cop3"],
        "trace+cop2": extra_runs["trace_cop2"],
        "trace+cop3+cop2": replay_runs["k3"],
    }
    for name, raw in factorial_sources.items():
        reported = factorial[name]
        require(reported["classification"] == raw["classification"], f"{name} result drift")
        require(
            reported["target_eq_mpr_count"] == raw["target_eq_mpr_count"],
            f"{name} Eq.mpr count drift",
        )
        require(
            reported["entered_fresh_replay"] == raw["entered_fresh_replay"],
            f"{name} replay marker drift",
        )
    for name in ("none", "cop2", "cop3", "cop3+cop2"):
        require(factorial[name]["classification"] == "accepted", f"{name} did not accept")
    for name in ("trace", "trace+cop3", "trace+cop2", "trace+cop3+cop2"):
        require(
            factorial[name]["classification"] == "deep_recursion",
            f"{name} did not deep-recurse",
        )
        require(factorial[name]["entered_fresh_replay"], f"{name} never entered replay")

    positions = summary["position_controls"]
    raw_positions = load_json(RUNS / "position-controls.json")["variants"]
    for name in ("pos_trace_last", "pos_cop2_first"):
        require(
            positions[name]["classification"] == raw_positions[name]["classification"],
            f"{name} position result drift",
        )
        require(raw_positions[name]["target_eq_mpr_count"] == 1, f"{name} is not one transport")
    require(positions["pos_trace_last"]["classification"] == "accepted", "trace-last failed")
    require(
        positions["pos_cop2_first"]["classification"] == "deep_recursion",
        "cop2-first did not fail",
    )
    raw_graphs = load_json(RUNS / "graph-audits.json")
    for name in ("k1", "trace", "pos_trace_last", "pos_cop2_first"):
        reported = positions[
            {"k1": "original_k1", "trace": "original_trace"}.get(name, name)
        ]["graph"]
        require(reported["transport_depth"] == raw_graphs[name]["transport_depth"], f"{name} depth drift")
        require(
            reported["transport_tree_size"] == raw_graphs[name]["transport_tree_size"],
            f"{name} tree-size drift",
        )
    structural = load_json(EVIDENCE / "transport-structural-equality.json")
    for name, comparison in structural["comparisons"].items():
        require(comparison["result"] == "structurally_equal", f"{name} expressions differ")
        require(comparison["exit_code"] == 0, f"{name} structural comparison failed")

    raw_subterms = load_json(RUNS / "subterm-checks.json")
    for name, record in summary["isolated_transport_checks"].items():
        require(record["isolated_maximal_eq_mpr"] == "failed", f"{name} subterm passed")
        require(
            raw_subterms[name]["maximal_transport_result"]
            == record["isolated_maximal_eq_mpr"],
            f"{name} isolated result drift",
        )

    raw_diagnostics = load_json(RUNS / "diagnostics.json")
    for name, reported in summary["accepted_target_kernel_diagnostics"].items():
        raw = raw_diagnostics[name]
        require(raw["target_accepted_ms"] == reported["target_accepted_ms"], f"{name} time drift")
        require(raw["unfold_events"] == reported["unfold_events"], f"{name} unfold drift")
        for watched, count in reported["watched_unfold_counts"].items():
            require(count == 0, f"{name} reports a watched unfold for {watched}")
            require(watched not in raw["all_unfolds"], f"{name} raw diagnostics unfold {watched}")

    raw_cache = load_json(RUNS / "cache-probes.json")["modes"]
    for name, reported in summary["accepted_trace_last_non_eqv_cache_controls"].items():
        require(reported["outcome"] == raw_cache[name]["outcome"], f"{name} cache outcome drift")
        require(reported["target_ms"] == raw_cache[name]["target_ms"], f"{name} cache time drift")

    intervention = summary["authoritative_trace_last_equivalence_interventions"]
    baseline = intervention["runs"]["baseline"]
    reset = intervention["runs"]["reset_eqv_before_transport"]
    no_add = intervention["runs"]["no_eqv_add"]
    require(baseline["outcome"] == "accepted", "causal baseline did not accept")
    require(reset["outcome"] == "deep_recursion", "surgical reset did not fail")
    require(no_add["outcome"] == "deep_recursion", "global no-add control did not fail")
    require(no_add["equivalence"]["adds"]["actual"] == 0, "no-add control added equivalences")
    require(reset["failure_phase"] == "argument-defeq", "wrong kernel failure phase")
    require("Eq.mpr/4" in reset["failure_app"], "failure was not Eq.mpr argument four")
    require(
        baseline["equivalence"]["watched_transport"]["initial_cached_hits"]
        == reset["equivalence"]["watched_transport"]["initial_cached_hits"]
        == 22,
        "baseline and intervention did not start from the same 22 cached hits",
    )
    require(
        reset["equivalence"]["watched_transport"]["reset_before"] == 1,
        "surgical equivalence reset was not active",
    )
    require(reset["whnf"]["max_nested_calls"] > 10_000, "missing deep WHNF recursion")
    require(baseline["whnf"]["max_nested_calls"] < 100, "baseline WHNF unexpectedly deep")
    require(
        reset["unfold"]["polynomial_power_recurrence"]["npowRec._f"] > 5_000,
        "missing polynomial-power recursion",
    )
    for name, count in reset["unfold"]["watched"].items():
        require(count == 0, f"unexpected watched unfold {name}={count}")

    baseline_text = (EVIDENCE / "trace-last-baseline.txt").read_text()
    reset_text = (EVIDENCE / "trace-last-reset-eqv.txt").read_text()
    no_add_text = (EVIDENCE / "trace-last-no-eqv-add.txt").read_text()
    old_first_text = (EVIDENCE / "old-first-transport.txt").read_text()
    require(
        baseline_text.startswith("parsed 20321 declarations\n")
        and reset_text.startswith("parsed 20321 declarations\n")
        and no_add_text.startswith("parsed 20321 declarations\n"),
        "trace-last logs do not match the full solution-export declaration count",
    )
    require(
        old_first_text.startswith("parsed 20319 declarations\n"),
        "old-first log does not match the exact pre-wrapper export declaration count",
    )
    require("target accepted in 4 ms" in baseline_text, "baseline acceptance marker absent")
    require("reset-before=1" in reset_text, "reset marker absent")
    require("INFER_FAILURE_PHASE argument-defeq" in reset_text, "failure marker absent")
    require("(kernel) deep recursion detected" in reset_text, "deep-recursion marker absent")
    require("EQV_ADD attempts=143 actual=0" in no_add_text, "no-add marker absent")
    require(
        scalar_counter(baseline_text, "WHNF_MAX_NESTED_CALLS")
        == baseline["whnf"]["max_nested_calls"],
        "baseline WHNF counter drift",
    )
    require(
        scalar_counter(reset_text, "WHNF_MAX_NESTED_CALLS")
        == reset["whnf"]["max_nested_calls"],
        "reset WHNF counter drift",
    )
    require(
        scalar_counter(reset_text, "FAILED_UNFOLD_TOTAL")
        == reset["unfold"]["total"],
        "reset unfold total drift",
    )
    reset_unfolds = unfold_counts(reset_text)
    require(
        reset_unfolds.get("npowRec._f", 0)
        == reset["unfold"]["polynomial_power_recurrence"]["npowRec._f"],
        "npowRec count drift",
    )
    reset_watched = watched_unfold_counts(reset_text)
    require(reset_watched == reset["unfold"]["watched"], "watched-unfold counters drift")
    require(all(count == 0 for count in reset_watched.values()), "watched definition unfolded")
    watched_line = re.search(
        r"^WATCHED_DEFEQ .*initial-cached-hits=(\d+).*reset-before=(\d+)$",
        reset_text,
        re.MULTILINE,
    )
    require(watched_line is not None, "reset equivalence-state marker absent")
    require(watched_line.groups() == ("22", "1"), "wrong reset equivalence-state values")

    if args.lean_src is not None:
        verify_lean_checkout(args.lean_src.resolve())

    print("evidence verified: no cardinality unfolding; Eq.mpr/4 enters polynomial npowRec")
    print("causal control verified: preserving equivalence state accepts; surgical reset fails")
    print("archived guard-bypass record consistency verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except EvidenceError as error:
        raise SystemExit(f"evidence verification failed: {error}")
