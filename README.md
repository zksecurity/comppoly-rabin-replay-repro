# CompPoly Rabin replay reproduction

This repository reproduces a resource blow-up while Comparator replays
`KoalaBear.sexticPoly_irreducible`, and demonstrates why the generic
explicit-cardinality wrapper avoids it.

The old proof places `Eq.mpr` transports around concrete certificates.
Replaying one transport makes Lean compare two definitionally equal certificate
types and normalize:

```text
Eq.mpr argument 4
  → Polynomial.pow
  → npowRec
  → Nat.rec
```

The instrumented run records zero unfolds of `Fintype.card`,
`Fintype.elems`, `ZMod.card`, and `ZMod.fintype`. This replay failure is therefore
polynomial-power normalization, not field enumeration. This is a
proof-shape/performance issue, not a soundness issue.

## The source-level change

The old concrete proof rewrites certificate goals after applying the generic
Rabin theorem:

```lean
have hcard : Fintype.card Field = fieldSize := ZMod.card _
refine irreducible_of_rabin_degree_six
  sexticPoly_natDegree ?_ ?_ ?_
· rw [hcard]
  exact traceCertificate
```

The fixed proof supplies the explicit cardinality before the certificates:

```lean
have hcard : Fintype.card Field = fieldSize := ZMod.card _
refine irreducible_of_rabin_degree_six_of_card
  fieldSize hcard sexticPoly_natDegree ?_ ?_ ?_
· exact traceCertificate
```

This fixes `q := fieldSize` before the certificate arguments are checked, so
their concrete `Eq.mpr` wrappers disappear. The wrapper remains generic over
any finite field; `ZMod.card` merely proves the equality at this call site.

## Run the examples

The fast control requires Python 3, Elan, and the pinned Lean toolchain:

```sh
elan toolchain install leanprover/lean4:v4.32.2
./small-exponent.sh
```

It checks with the stock Lean kernel that exponents 4, 8, and 16 cause exactly
4, 8, and 16 multiplication unfolds, and proves that a complete symbolic
`npowRec` expansion has exactly `n` multiplication nodes.

The Docker experiments are:

| Command | Expected result |
| --- | --- |
| `./repro.sh --suite original` | The old `rw` proof deep-recurses or exceeds its bound; the wrapper proof is accepted. |
| `./mechanism.sh` | The exact old serialized target replay reaches `Eq.mpr/4 → Polynomial.pow → npowRec`, with zero cardinality/enumerator unfolds. |
| `./repro.sh --suite followup` | Both the explicit- and implicit-`q` wrapper revisions are accepted. |
| `./growth.sh --confirm` | Bypasses Lean's stack guard under external limits; memory grows by at least 2 GiB before the 6 GiB watchdog stops it. |
| `./growth.sh --memory-gib 40 --confirm-large-run` | Runs the same guard bypass to a 40 GiB watchdog under a 48 GiB/no-swap hard cap. |

The Docker experiments require Bash, Docker, and at least 8 GiB of memory; the
growth experiment additionally requires cgroup v2. The first `mechanism.sh` or
`growth.sh` run builds pinned Lean source; allow roughly 30–40 GiB of free disk
space. The diagnostic source build has been validated on arm64; validate it
separately before relying on amd64.

For the 40 GiB profile, Docker must report at least 56 GiB total and the
container must see 44 GiB currently available. This may require assigning more
than 56 GiB to a Docker VM. Use a disposable machine with roughly 64 GiB or
more of RAM and close other workloads first. Its 40 GiB measurement is
whole-container cgroup memory after target entry, not Lean process RSS.

One validated guard-bypass run grew from 2.746 GiB to 6.001 GiB in 16.9
seconds. This is a bounded observation, not a benchmark or an extrapolation.

To remove only the two diagnostic images:

```sh
./mechanism.sh --clean
```

This does not prune Docker's shared build cache.

## Safety

Docker image construction has network access and executes pinned upstream
sources. Experiment containers run offline with no cgroup swap; their hard cap
is 8 GiB normally and 48 GiB only for the separately confirmed large profile.

Both growth profiles deliberately bypass Lean's internal stack guard. The
default keeps an 8 GiB cgroup limit and stops at 6 GiB; the separately confirmed
large profile keeps a 48 GiB limit and stops at 40 GiB. Neither mode treats OOM
as success. Do not run either in CI. See [SAFETY.md](SAFETY.md) for the complete
trust and resource boundaries.

## Details and evidence

- [Kernel mechanism and full experimental analysis](deep-dive/README.md)
- [Machine-readable results](deep-dive/evidence/summary.json)
- [Exact revisions and export hashes](deep-dive/evidence/manifest.json)
- [Evidence lineage and archived observations](deep-dive/evidence/README.md)

Check the committed evidence without rerunning the expensive experiments:

```sh
python3 deep-dive/verify_evidence.py
```

Upstream changes:

- [Original explicit-`q` wrapper](https://github.com/zksecurity/CompPoly/commit/641694629e4557520a1539b272ec338c9f3044c7)
- [Merged wrapper](https://github.com/Verified-zkEVM/CompPoly/commit/32a0c29e41225e8cec2a2e1eab1dfab64f026aa0)
- [Implicit-`q` follow-up](https://github.com/Verified-zkEVM/CompPoly/commit/7480a691ff87d178f0d0afd45454d8400e39e268)

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for provenance and
licensing.
