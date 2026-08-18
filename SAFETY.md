# Safety and trust boundary

This repository reproduces a resource-exhaustion pathology. Use only the exact
trusted revisions pinned by the scripts.

- Docker image construction has network access and executes code fetched from
  the pinned upstream repositories. Do not mount secrets into the build or run.
- Experiment containers run with networking disabled, no cgroup swap, at most
  four CPUs, bounded process counts, and in-container timeouts. Normal and
  default growth runs use an 8 GiB memory cap; the separately confirmed large
  profile uses 48 GiB. Normal runs impose an 8 MiB OS main-thread stack limit;
  Lean 4.32 normally dispatches its program main to a separately allocated
  1 GiB worker-thread stack.
- Docker image construction is separate from those runtime cgroup limits. The
  Lean source build is limited to at most four parallel jobs, but may still use
  substantial CPU, memory, disk, and time.
- Git revisions, the Ubuntu base-image digest, Elan archives, and the diagnostic
  patch are pinned. Ubuntu packages and auxiliary dependencies fetched by
  Lean's CMake build are not a byte-for-byte reproducible supply-chain closure;
  the runtime therefore verifies the resulting version, source diff, export
  hash, and observed kernel path rather than trusting a binary hash.
- Comparator's `fake-landrun.sh` is sufficient for this trusted performance
  reproduction; it is not a sandbox for hostile Lean input.
- The instrumented kernel records checking behavior. It is a diagnostic build,
  not a proposed Lean fix and not an independent proof checker.
- Docker daemon access is privileged. Review the scripts and pins before use.
- Keep the known-pathological replay opt-in/manual; do not run it automatically
  on pull requests or against arbitrary forks.

`./growth.sh --confirm` is intentionally more aggressive than the other runs.
It gives the OS main thread an unlimited stack and sets
`LEAN_MAIN_USE_THREAD=0`, which bypasses Lean 4.32.2's internal stack guard.
That does **not** mean the run is unbounded: the default container remains
offline with an 8 GiB/no-swap cgroup, and the in-container watchdog kills the
child at 6 GiB.

`./growth.sh --memory-gib 40 --confirm-large-run` is a second, much larger
profile. It stops at 40 GiB of whole-container `memory.current` under a 48 GiB
hard cap, uses no cgroup swap, and times out after 600 seconds. It refuses to
start unless Docker reports at least 56 GiB total and the container sees at
least 44 GiB currently available. A Docker VM may need more than 56 GiB
assigned before it reports that much. Use a disposable machine with roughly
64 GiB or more of RAM, reserve it for this experiment, and close other workloads.
Container swap is disabled, but the script cannot prevent a host or Docker VM
from applying its own memory-management policy.

For both profiles, the runner fails if Docker records an OOM, if the target
accepts or crashes, or if the expected exact target body check was never
entered. The 40 GiB result is cgroup memory for the complete experiment
container after the exact target marker, not process RSS, not an additional
40 GiB after target entry, and not memory attributed exclusively to `npowRec`.

The archived 58-GiB curve is evidence from a prior disposable external host,
not a suggested local configuration. No script in this repository requests a
58-GiB container or treats a cgroup OOM as success.

Remove only this experiment's images with:

```sh
./mechanism.sh --clean
```

`./growth.sh --clean` is an alias for the same narrowly scoped image cleanup.

That command does not prune unrelated images, volumes, or the global build
cache. The full Lean build can leave tens of gigabytes in that shared cache;
this repository deliberately does not run a broad `docker builder prune`.
