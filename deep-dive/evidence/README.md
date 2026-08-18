# Evidence lineage

The live mechanism experiment is `../../mechanism.sh`. The files here preserve
the earlier macOS arm64 investigation and allow its claims to be checked
without committing roughly 98 MB exports or platform-specific binaries.

Authoritative mechanism records:

- `old-first-transport.txt` is the final-patch diagnostic of the exact old
  `6133f9f` theorem export.
- `trace-last-baseline.txt` and `trace-last-reset-eqv.txt` are the matched
  final-patch causal pair on the controlled trace-last export.
- `trace-last-no-eqv-add.txt` is the final-patch coarse equivalence-state
  control.
- `manifest.json` owns their source/export/log hashes and commands.
- `summary.json` is the normalized report checked against the raw records.

The `runs/` files are archived observations from the controlled screening:

- `results.json` contains the initial 4 GiB screen. A pre-replay resource
  termination there is not treated as a kernel result.
- `replays-8g.json` and `subsets-extra.json` supersede it for the complete
  eight-arm, 8 GiB subset matrix.
- `position-controls.json`, `subterm-checks.json`, and `graph-audits.json`
  record the crossover and structural controls.
- `diagnostics.json` records accepted-target checks.
- `cache-probes.json` contains secondary controls built during the probe
  iteration. The final causal conclusion relies on the final-patch logs above,
  not on those earlier corroborating runs.

The guard-bypass resource record is a separate evidence family:

- `archived-guard-bypass-58g.txt` is a normalized transcription of the original
  cgroup-v2 sampling output;
- `archived-guard-bypass-58g.json` records its exact program hash, relevant
  source pins, runtime limits, metric scope, outcome, derived fit, and
  nonclaims; and
- `../probes/ArchivedDirectReplay.lean` is byte-identical to the 868-byte
  program used in that run.

This was a direct `Environment.replay` of an imported dependency closure, not a
Comparator/lean4export run. It is an archived external-host observation and is
not rerun by the default verifier. The verifier hashes the normalized inputs,
re-parses the curve, and recomputes the regression; it cannot independently
recreate a deleted historical container image. The live, bounded equivalent
for the exact serialized target is `../../growth.sh --confirm`.

Some raw records retain their original `/private/tmp` log paths. Those paths
are provenance strings, not files expected to exist on another machine. The
committed verifier derives claims only from the committed records and logs.
It checks integrity and internal consistency; it is not a fresh replay.
