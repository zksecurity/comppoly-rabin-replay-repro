# Kernel deep dive: what actually recurses

The earlier “field enumeration” explanation is false for this replay failure.
On the exact Lean 4.32.2 kernel path, neither `Fintype.card` nor a finite-field
enumerator is unfolded. The kernel instead tries to establish definitional
equality between two encodings of the transported certificate's proposition,
falls into weak-head normalization of an enormous polynomial power, and
recurses through `npowRec` / `Nat.rec` until Lean's proactive stack guard fires.

The causal path is:

```text
Eq.mpr ... certificate
        │ kernel checks argument 4 against Eq.mpr's source proposition
        ▼
given:    Polynomial (ZMod fieldSize) (... ZMod.instField ...)
expected: Polynomial KoalaBear.Field  (... KoalaBear.instFieldField ...)
        │ definitionally equal, but not the same reconstructed expression
        ▼
is_def_eq → lazy delta reduction → weak-head normalization
        ▼
Polynomial.pow → npowRec → Nat.rec → polynomial multiplication
        ▼
11,566 nested WHNF calls → kernel deep-recursion exception
```

Both endpoints already contain the same `fieldSize ^ 6`. The kernel is not
trying to discover `fieldSize`; it is reconciling the concrete `ZMod`
representation and named-instance paths around that exponent.

## Small-exponent control: why the unfolding is linear

Run the fast stock-kernel control from the repository root:

```sh
./small-exponent.sh
```

The pinned Mathlib polynomial-power instance delegates directly to Lean's
`npowRec`:

```lean
instance (priority := 1) pow : Pow R[X] ℕ where
  pow p n := npowRec n p
```

The pinned Lean definition then removes one successor and appends one
multiplication per recursive call:

```lean
def npowRec [One M] [Mul M] : Nat → M → M
  | 0, _ => 1
  | n + 1, a => npowRec n a * a
```

[`SmallExponent.lean`](probes/SmallExponent.lean) uses that actual `npowRec`
primitive with a symbolic multiplication tree. Its exponent-4, exponent-8,
and exponent-16 checks, as well as the generic successor equation
`atom^(n + 1) = atom^n * atom`, are all proved by `rfl`; no exponentiation
theorem or simplification tactic is used. Lean's built-in, unmodified kernel
diagnostics report:

| Exponent `n` | `npowRec` case visits | `Mul.mul` unfolds |
| ---: | ---: | ---: |
| 4 | 5 | 4 |
| 8 | 9 | 8 |
| 16 | 17 | 16 |

The extra case visit is the base case. `#reduce` also prints exponent four as:

```text
(((PowTrace.one.mul PowTrace.atom).mul PowTrace.atom).mul PowTrace.atom).mul PowTrace.atom
```

The same file proves the following statement for arbitrary `n`, not just the
three measured examples:

```lean
theorem PowTrace.mulNodes_pow_atom (n : Nat) :
    (PowTrace.atom ^ n).mulNodes = n
```

Therefore a complete `npowRec` expansion contains exactly `n` multiplication
nodes. In an explicit-expression representation, materializing that expansion
has Θ(`n`) output size and therefore requires Ω(`n`) work as `n` grows. This is
not a claim that the total cost is exactly linear: reducing the polynomial
multiplications and performing definitional equality may add superlinear work.

Here `n = fieldSize ^ 6` is
`93,571,093,019,388,561,295,270,373,781,649,880,353,786,165,192,103,559,169`.
[`SmallExponent.lean`](probes/SmallExponent.lean) also proves this exceeds the
number of bits in 40 GiB. Even assigning the impossible minimum of one bit to
each multiplication node, a complete explicit tree cannot fit in 40 GiB. This
is a lower bound for explicit materialization, not for the kernel's peak
resident memory: an evaluator can consume and release intermediate nodes.

The small control establishes this local lower bound independently of the
custom kernel patch. The large instrumented replay establishes the other half
of the claim: the failing certificate-type comparison enters this same
`Polynomial.pow` / `npowRec` path. It reaches only about 5,800 case visits
before Lean's stack guard fires; it does not finish the `fieldSize ^ 6`
countdown. Other checking contexts may shortcut the comparison using existing
equivalence state, so the evidence does not imply an unconditional
Ω(`fieldSize ^ 6`) runtime bound for every replay.

The exact upstream definitions are pinned at Lean
[`f3b06c7`](https://github.com/leanprover/lean4/blob/f3b06c705e6c85f5314019d5d3baab0fec5b580c/src/Init/Data/Zero.lean#L35-L43)
and Mathlib
[`905b958`](https://github.com/leanprover-community/mathlib4/blob/905b95818eb32af7874a58b427f50c1711a5e96c/Mathlib/Algebra/Polynomial/Basic.lean#L141).

## Evidence at a glance

The controlled transport variants and causal intervention pin:

- CompPoly `641694629e4557520a1539b272ec338c9f3044c7`;
- Lean 4.32.2, source commit
  `f3b06c705e6c85f5314019d5d3baab0fec5b580c`;
- Comparator `51491237b1d2f96cca203af9c34bced6fe38e0d8`;
  and
- lean4export `af5aa64bb914c3c2c781f378088dbd38acf4f804`.

The separately archived original-proof diagnostic uses the direct pre-wrapper
CompPoly revision `6133f9f796707c438d0a614f97dc218ae976ab8f` under the same
Lean, Comparator, and lean4export pins. Its distinct export revision and hash
are recorded in [`evidence/manifest.json`](evidence/manifest.json).

## One-command live mechanism experiment

From the repository root, run:

```sh
./mechanism.sh
```

This is the shortest experiment that tests the mechanism rather than merely
checking the archived evidence. It performs the following fail-closed chain:

```text
pinned old CompPoly theorem
  -> pinned lean4export closure (exact SHA-256 required)
  -> fresh-environment dependency replay
  -> target-only check with pinned instrumented Lean kernel
  -> parse and require the Eq.mpr/4, endpoint, WHNF, and unfold markers
```

The command succeeds only when the target reaches `checker.check(value)`,
fails during the fourth argument comparison of `Eq.mpr`, prints the concrete
`ZMod` and named `KoalaBear.Field` endpoints, records positive
`Polynomial.pow`/`npowRec`/`Nat.rec` activity, and records zero unfolds for
`Fintype.card`, `Fintype.elems`, `ZMod.card`, and `ZMod.fintype`.

The live check demonstrates the failure location, reduction family, and
absence of field-cardinality enumeration on this exact path. The separate
position and equivalence-state experiments below establish why checking order
can make the same transport accept or fail; they are not rerun by the minimal
default command.

The first run performs a full pinned Lean source build and can be slow and
disk-intensive; allow roughly 30 to 40 GiB of free space for image layers and
BuildKit cache. Image construction has network access and is outside the
runtime replay cgroup; compilation is limited to at most four parallel jobs.
The actual experiment runs offline under an 8 GiB/no-swap cgroup, an 8 MiB OS
main-thread stack limit, and explicit process-group timeouts. Lean normally
runs the program main on its own 1 GiB worker-thread stack; the kernel guard is
based on that worker stack, not the 8 MiB main-thread limit. See
[`../SAFETY.md`](../SAFETY.md).

## Opt-in guard-bypass continuation

The focused mechanism run stops at Lean's proactive `check_stack` guard. To
observe what happens after that guard, run from the repository root:

```sh
./growth.sh --confirm
```

For an explicitly confirmed large run of the same target:

```sh
./growth.sh --memory-gib 40 --confirm-large-run
```

This is a separate, deliberately resource-intensive mode. It uses the same
exact old 98 MB export, `DiagnosticReplay.lean`, and instrumented Lean kernel,
but starts the Lean program with:

```text
RLIMIT_STACK = unlimited
LEAN_MAIN_USE_THREAD = 0
```

Lean initializes stack checking on the OS main thread before dispatching the
program main. At this pinned revision, an unlimited stack makes the unsigned
threshold calculation overflow; Lean detects that overflow and records
threshold zero. `LEAN_MAIN_USE_THREAD=0` then keeps the replay on that main
thread, so `check_stack` cannot reach its throw condition. This bypasses the
internal guard; it does not disable the experiment's external protections.

The runner verifies the pinned source branch implementing both behaviors,
requires cgroup v2 and zero swap, and samples `memory.current` four times per
second. The default profile stops at 6 GiB under an 8 GiB cap. The `large-40`
profile stops at 40 GiB under a 48 GiB cap, has a 600-second timeout, requires
Docker to report at least 56 GiB total and 44 GiB currently available, and
requires at least 30 GiB of growth after the exact target marker. A Docker VM
may need more than 56 GiB assigned before it reports that much. A successful
result requires:

- dependency replay accepted and the exact
  `KoalaBear.sexticPoly_irreducible` body check started;
- sampled whole-container memory increased by at least 2 GiB in the default
  profile or 30 GiB in the large profile;
- the fitted memory trend is positive;
- the userspace watchdog delivered the terminating `SIGKILL`;
- the cgroup `oom` and `oom_kill` counters did not change; and
- no target-acceptance, deep-recursion, or native-crash marker appeared.

In one validated Linux/arm64 run, the target marker appeared at 16.5 seconds
and 2.746 GiB. The watchdog stopped it at 33.4 seconds and 6.001 GiB: 3.255 GiB
of target-scoped growth, with an ordinary least-squares fit of 0.188 GiB/s
(R² rounded to 1.000). This single bounded run is an observation, not a
performance threshold or an extrapolation beyond the measured window.

That validated transcript used the default 6/8 GiB profile. The 40 GiB profile
is a live, fail-closed stress mode rather than a prerecorded result: it reports
success only if the exact serialized target reaches 40 GiB of whole-container
`memory.current` and its userspace watchdog stops the child before OOM. Do not
describe it as a 40 GiB observation unless that command itself prints `PASS`.

The live continuation establishes growth for the exact serialized target. The
normal mechanism run independently establishes that this target's failing path
is `Eq.mpr/4 → Polynomial.pow → npowRec`, with zero cardinality/enumerator
unfolds. The growth classifier does not pretend that aggregate cgroup memory
alone identifies which internal structure owns every byte.

### Archived 58-GiB continuation

A prior external-host program invoked a direct `Environment.replay` of the
imported old-theorem closure under the same guard bypass. The exact 868-byte
program is committed as
[`ArchivedDirectReplay.lean`](probes/ArchivedDirectReplay.lean). It did not use
Comparator or lean4export, so it is supporting stress evidence rather than the
serialized-boundary experiment above.

Its whole-container cgroup-v2 samples were:

| Nominal seconds | `memory.current` GiB |
| ---: | ---: |
| 0 | 1.9 |
| 20 | 7.6 |
| 40 | 12.8 |
| 60 | 17.1 |
| 80 | 22.2 |
| 100 | 26.9 |
| 120 | 31.0 |
| 140 | 34.0 |
| 160 | 38.4 |
| 180 | 42.4 |
| 200 | 46.2 |
| 220 | 49.7 |
| 240 | 53.0 |
| 260 | 56.0 |

The ordinary least-squares fit over those rounded samples is 0.206 GiB/s with
R² 0.994. Before the nominal 280-second poll, the 58-GiB/no-swap cgroup
OOM-killed the unfinished container; 56.0 GiB is the last valid sample, not an
exact peak or process RSS. The committed
[`JSON record`](evidence/archived-guard-bypass-58g.json) and normalized
[`transcript`](evidence/archived-guard-bypass-58g.txt) preserve the pins,
measurement scope, and nonclaims. `verify_evidence.py` hashes their inputs,
re-parses the samples, and recomputes the fit.

The controlled transport measurements are in
[`evidence/summary.json`](evidence/summary.json). The full Comparator arms used
an 8 GiB process-group RSS watchdog, a 120-second wall bound, one Lean thread,
and an inherited approximately 8 MiB OS main-thread RLIMIT; the Lean program
main normally used its separate worker stack. Times are observed transcripts,
not benchmark thresholds.

### 1. All eight transport subsets

The three Rabin conditions were independently discharged either through the
small generic adapter or through the original concrete `rw [hcard]` transport.
The elaborated `@Eq.mpr` count was checked before export.

| Concrete caller-side transports | Result in original condition order |
| --- | --- |
| none | accepted |
| `cop2` | accepted |
| `cop3` | accepted |
| `cop3 + cop2` | accepted |
| `trace` | deep recursion |
| `trace + cop3` | deep recursion |
| `trace + cop2` | deep recursion |
| `trace + cop3 + cop2` | deep recursion |

This rules out “three transports are required,” but by itself still confounds
the trace proposition with its first condition-proof position.

### 2. Position crossover

A shared reversed-order adapter separates proposition identity from
application-spine position:

| One unchanged transport | Position | Result |
| --- | --- | --- |
| trace transport, expanded tree size 2,743 | first | deep recursion |
| same trace transport, expanded tree size 2,743 | last | accepted |
| cop2 transport, expanded tree size 2,175 | last | accepted |
| same cop2 transport, expanded tree size 2,175 | first | deep recursion |

`CompareTransport.lean` parses each pair of exports, extracts the sole maximal
`Eq.mpr`, and checks exact Lean `Expr` structural equality. Both moved pairs are
equal—not merely equal in depth and expanded tree size. Thus neither
certificate identity, chain length, transport count, nor transport subgraph
shape alone determines the outcome. Checking order and the surrounding
type-checker state do.

Checking each maximal `Eq.mpr` in isolation provides a second control: all four
one-transport subterms deep-recurse cold, including both transports whose full
theorems accept. The accepting application spine has therefore primed state
that an isolated check does not have.

### 3. Surgical equivalence-state intervention

The final instrumentation adds a diagnostic environment variable that acts at
one location only: immediately before the fourth-argument type comparison of
the watched `Eq.mpr`. It replaces `type_checker.state.m_eqv_manager` with a
fresh manager and leaves the already populated inference, WHNF, unfolding, and
failure caches untouched.

The same parsed trace-last export, runner, dylib, theorem, and process limits
produce opposite outcomes:

| Exact run | Initial cached equivalence hits | Max nested WHNF | Result |
| --- | ---: | ---: | --- |
| baseline | 22 | 26 | accepted in 4 ms |
| reset only before `Eq.mpr` argument 4 | 22, then reset | 11,566 | deep recursion in 5,188 ms |

This is the causal intervention. State accumulated before the final argument
comparison in the surrounding application/body check is sufficient to avoid
the bad reduction path; removing only that equivalence state makes the
otherwise identical late transport fail. The position crossover establishes
an order effect, but the intervention does not attribute each cached entry to
a particular earlier argument. The equivalence classes contain redundant
paths, so this work does not claim a single unique cached pair.

For comparison, disabling inference and WHNF caches, separately and together,
does not make the trace-last theorem fail. Globally suppressing equivalence
additions does make it fail, but earlier at the first direct certificate; that
coarser test corroborates the surgical result rather than replacing it.

## Exact kernel failure

Instrumentation around `environment::add_theorem` establishes that the target
passes:

- expression sharing;
- theorem-type checking;
- the `isProp` check; and
- the free-variable/metavariable check.

It fails inside `checker.check(value)`, before the final comparison of the
inferred proof type with the theorem's declared type. The discarded stack
component is exactly:

```text
deep recursion was detected at 'type checker: whnf'
```

The failing application is the fourth argument of `Eq.mpr`; its argument is
the opaque theorem reference
`CompPoly.RabinCert.dvd_X_pow_sub_X_of_runChain`. The first three application
prefixes check successfully. Adding the certificate proof asks the kernel to
compare these proposition endpoints:

```text
given:
  Dvd ...
    (Polynomial (ZMod KoalaBear.fieldSize)
      (... ZMod.instField fieldSize KoalaBear.instFactPrimeFieldSize ...))
    ... (X ^ (KoalaBear.fieldSize ^ 6) - X)

expected:
  Dvd ...
    (Polynomial KoalaBear.Field
      (... KoalaBear.instFieldField ...))
    ... (X ^ (KoalaBear.fieldSize ^ 6) - X)
```

The reset run records:

| Counter | Value |
| --- | ---: |
| WHNF entries | 336,869 |
| maximum nested WHNF calls | 11,566 |
| recursor steps | 11,559 |
| beta steps | 86,904 |
| delta steps | 40,669 |
| total recorded unfolds | 52,228 |
| `npowRec._f` unfolds | 5,779 |
| `npowRec.match_1` unfolds | 5,779 |

`fieldSize` is 2,130,706,433, so the proposition contains:

```text
fieldSize ^ 6 =
93571093019388561295270373781649880353786165192103559169
```

The kernel begins reducing polynomial exponentiation at this exponent and hits
its stack guard long before normalization could finish.

The pinned upstream implementation points are Lean's
[`environment::add_theorem`](https://github.com/leanprover/lean4/blob/f3b06c705e6c85f5314019d5d3baab0fec5b580c/src/kernel/environment.cpp#L192-L206),
[`infer_app` argument comparison](https://github.com/leanprover/lean4/blob/f3b06c705e6c85f5314019d5d3baab0fec5b580c/src/kernel/type_checker.cpp#L163-L177),
[`whnf_core`](https://github.com/leanprover/lean4/blob/f3b06c705e6c85f5314019d5d3baab0fec5b580c/src/kernel/type_checker.cpp#L398-L482),
[`is_def_eq`](https://github.com/leanprover/lean4/blob/f3b06c705e6c85f5314019d5d3baab0fec5b580c/src/kernel/type_checker.cpp#L1070-L1137),
the [`equiv_manager`](https://github.com/leanprover/lean4/blob/f3b06c705e6c85f5314019d5d3baab0fec5b580c/src/kernel/equiv_manager.cpp#L47-L126),
and the proactive [stack guard](https://github.com/leanprover/lean4/blob/f3b06c705e6c85f5314019d5d3baab0fec5b580c/src/runtime/stackinfo.cpp#L123-L133).

## Why this disproves enumeration

The failed run records every delta-unfold event on the path. Counts for all of
these are exactly zero:

```text
Fintype.card
Fintype.elems
ZMod.card
ZMod.fintype
Eq.mp
Eq.mpr
Eq.rec
Eq.ndrec
congrArg
```

`Eq.mpr` need not itself unfold for the bug to appear: merely applying the
constant requires its fourth argument to have the source proposition. That
argument-type definitional-equality check is where polynomial normalization
starts.

The exported old and wrapper proofs both still contain the `Fintype.card` and
`ZMod.card` constants used by `hcard`. Their presence is harmless here. What the
wrapper removes from the concrete theorem is the three `Eq.mpr` applications
whose certificate arguments trigger these endpoint comparisons.

## Why the wrapper helps

The caller-side proof has this shape:

```lean
rw [hcard]
exact certificate
```

Its elaborated term wraps the concrete certificate in an `Eq.mpr`. During cold
replay the kernel must verify that the certificate's reconstructed inferred
type matches the transport's reconstructed source proposition. That is the
comparison described above.

The wrapper instead receives `q`, `hcard`, and all three certificate hypotheses
while the field is abstract, substitutes the equality generically, and invokes
the original Rabin theorem. At the concrete call, `q := fieldSize` is fixed
before certificate arguments are checked, so the certificates are direct
arguments with no concrete caller-side `Eq.mpr`.

The later implicit-`q` commit does not change this. Elaboration recovers
`q := fieldSize` from the right-hand side of
`hcard : Fintype.card Field = fieldSize`; exact closure comparison shows that
the concrete sextic proof expression remains unchanged.

## Reproduce and audit

The root [`repro.sh`](../repro.sh) is the resource-bounded, Docker-based
end-to-end Comparator reproduction. It uses unmodified Comparator and Lean and
should be run before the diagnostic experiments.

The root [`mechanism.sh`](../mechanism.sh) is the containerized live diagnostic
described above. It first reruns the small-exponent control and verifies the
pinned polynomial-to-`npowRec` source bridge, then rebuilds the exact large
probe from source. It does not trust the committed result logs.

Verify the committed deep evidence and all cross-checks without downloading
large exports:

```sh
python3 deep-dive/verify_evidence.py
```

That verifier is an integrity/self-consistency audit of archived observations,
not a fresh execution. Their provenance and supersession rules are documented
in [`evidence/README.md`](evidence/README.md).

If an exact clean Lean source checkout is available, also verify that the
instrumentation patch applies to the pinned source (or is already applied):

```sh
python3 deep-dive/verify_evidence.py --lean-src /path/to/lean4-v4.32.2
```

The host-specific [macOS rebuild guide](BUILD_MACOS.md) records the exact
source preparation, exporter invocation and hash, kernel compilation/link, and
baseline/reset commands used for the causal probe. The large NDJSON exports
and platform-specific dylib are intentionally not committed.

The relevant files are:

- [`Dockerfile.mechanism`](Dockerfile.mechanism),
  [`run_mechanism.py`](run_mechanism.py), and
  [`instrumented-lean-wrapper.sh`](instrumented-lean-wrapper.sh): the live
  source build, bounded classifier, and loader-path isolation used by
  `mechanism.sh`;
- [`fixtures/TransportVariants.lean`](fixtures/TransportVariants.lean): the
  complete subset fixture;
- [`fixtures/pos-trace-last.patch`](fixtures/pos-trace-last.patch) and
  [`fixtures/pos-cop2-first.patch`](fixtures/pos-cop2-first.patch): the exact
  compiled position-crossover controls against the pinned CompPoly revision;
- [`probes/DiagnosticReplay.lean`](probes/DiagnosticReplay.lean): parses an
  exported closure, replays dependencies, then checks the target alone;
- [`probes/SmallExponent.lean`](probes/SmallExponent.lean) and
  [`check_small_exponent.py`](check_small_exponent.py): the stock-kernel
  linear-unfolding control and its fail-closed counter checker;
- [`probes/SubtermCheck.lean`](probes/SubtermCheck.lean): extracts and checks
  maximal `Eq.mpr` terms in isolation;
- [`probes/CompareTransport.lean`](probes/CompareTransport.lean): checks exact
  structural equality of the two moved-transport pairs;
- [`probes/analyze_export_graph.py`](probes/analyze_export_graph.py): streaming
  proof-graph metrics without loading a roughly 98 MB export into JSON objects;
- [`patches/lean-4.32.2-kernel-probe.patch`](patches/lean-4.32.2-kernel-probe.patch):
  the exact diagnostic kernel patch;
- [`evidence/trace-last-baseline.txt`](evidence/trace-last-baseline.txt) and
  [`evidence/trace-last-reset-eqv.txt`](evidence/trace-last-reset-eqv.txt): the
  opposing causal-control logs;
- [`evidence/trace-last-no-eqv-add.txt`](evidence/trace-last-no-eqv-add.txt):
  the independent global equivalence-addition control; and
- [`evidence/old-first-transport.txt`](evidence/old-first-transport.txt): the
  original first-position failure trace;
- [`evidence/transport-structural-equality.json`](evidence/transport-structural-equality.json):
  the bounded structural-comparison transcript and export hashes; and
- [`evidence/runs`](evidence/runs): raw bounded-run records from which the
  verifier derives the factorial, position, subterm, graph, diagnostic, and
  non-equivalence-cache claims; and
- [`evidence/manifest.json`](evidence/manifest.json): host/tool provenance,
  target-export hash, build-artifact hashes, exact environments, and log
  ownership for the causal runs.

The kernel patch is instrumentation, not a proposed Lean fix. Its SHA-256 is:

```text
25a72ee897a26b37ee9df3150427405457f910c069b658a5875596389cec57be
```

The instrumented macOS arm64 `libleanshared.dylib` used for the committed logs
had SHA-256:

```text
f0f19c8af87f6b92ea20ba91f47f9eef18b50e2d20279189ea564e66ae1c8282
```

The binary is intentionally not committed. The exact source patch, probe
source, final causal logs, hashes, revision manifest, and reconstruction recipe
are committed. The verifier checks their integrity and derives its claims from
the raw records/logs; rebuilding the host-specific dylib remains a separate
manual diagnostic procedure.

## Remaining boundary

This work identifies the exact failing kernel operation, reduction family, and
state dependency. It does not isolate one uniquely necessary equivalence pair:
the cached equivalence classes contain redundant paths through the field
abbreviation and typeclass-instance expressions.

It also does not claim a Lean soundness problem. Raising the stack limit or
changing checking order may alter whether this proof completes, but neither
changes which proofs the kernel accepts when checking terminates.
