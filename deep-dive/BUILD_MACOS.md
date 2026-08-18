# Rebuilding the diagnostic probe on macOS arm64

This is the host-specific reconstruction recipe for the committed diagnostic
logs. The root Docker harness remains the portable, unmodified-kernel
reproduction. Run this probe only on trusted exports and under external memory,
CPU, wall-time, and process-group limits.

The recorded run used macOS 15.7.5 arm64, Apple Clang 17.0.0 for the C++
compilation steps, and Clang 22.1.4 bundled with Lean 4.32.2 for the final
link. Set these paths for local checkouts:

```sh
REPRO=/path/to/comppoly-rabin-replay-repro
LEAN_SRC=/path/to/lean4-v4.32.2
TOOLCHAIN=/path/to/leanprover--lean4---v4.32.2
PROBE=/private/tmp/comppoly-kernel-probe
COMPPOLY=/path/to/comppoly-trace-last
COMPARATOR=/path/to/comparator
EXPORT=/private/tmp/pos_trace_last-solution.ndjson
```

## Prepare exact sources

The Lean checkout must be at:

```text
f3b06c705e6c85f5314019d5d3baab0fec5b580c
```

Apply and verify the committed diagnostic patch:

```sh
git -C "$LEAN_SRC" apply "$REPRO/deep-dive/patches/lean-4.32.2-kernel-probe.patch"
python3 "$REPRO/deep-dive/verify_evidence.py" --lean-src "$LEAN_SRC"
```

The CompPoly checkout must be at
`641694629e4557520a1539b272ec338c9f3044c7`. Apply the exact trace-last
position control and rebuild its affected module:

```sh
git -C "$COMPPOLY" apply "$REPRO/deep-dive/fixtures/pos-trace-last.patch"
cp "$REPRO/case/ReplayChallenge.lean" "$COMPPOLY/CompPoly/ReplayChallenge.lean"
cp "$REPRO/case/ReplaySolution.lean" "$COMPPOLY/CompPoly/ReplaySolution.lean"
cd "$COMPPOLY"
ELAN_TOOLCHAIN=leanprover/lean4:v4.32.2 \
  lake --no-cache build \
  CompPoly.ReplayChallenge CompPoly.ReplaySolution
```

Comparator must be at
`51491237b1d2f96cca203af9c34bced6fe38e0d8`; its `lean4export` package must be
at `af5aa64bb914c3c2c781f378088dbd38acf4f804`, both built under Lean 4.32.2.
The root Dockerfile contains the fail-closed pin and build checks.

Export the exact target closure:

```sh
cd "$COMPPOLY"
ELAN_TOOLCHAIN=leanprover/lean4:v4.32.2 \
  lake env "$COMPARATOR/.lake/packages/lean4export/.lake/build/bin/lean4export" \
  CompPoly.ReplaySolution -- ReplayDemo.target > "$EXPORT"
shasum -a 256 "$EXPORT"
```

The expected SHA-256 is:

```text
3230ba9e3c41a1b1a01617c5be60b69ad4e3cff3460e7d4cd4c57de89ab78175
```

## Build the instrumented shared library

Create the probe directory and its exact source-revision header:

```sh
mkdir -p "$PROBE"
printf '%s\n' \
  '// Instrumented probe build; exact upstream revision is supplied here.' \
  '#define LEAN_GITHASH "f3b06c705e6c85f5314019d5d3baab0fec5b580c"' \
  > "$PROBE/githash.h"
cp "$TOOLCHAIN/lib/lean/libleancpp.a" "$PROBE/libleancpp-instrumented.a"
```

Compile the four changed/runtime translation units:

```sh
clang++ -std=c++20 -O3 -DNDEBUG -DLEAN_EXPORTING -fPIC -fvisibility=hidden \
  -I"$PROBE" -I"$LEAN_SRC/src" -I"$TOOLCHAIN/include" \
  -c "$LEAN_SRC/src/util/shell.cpp" -o "$PROBE/shell.cpp.o"
clang++ -std=c++20 -O3 -DNDEBUG -DLEAN_EXPORTING -fPIC -fvisibility=hidden \
  -I"$LEAN_SRC/src" -I"$TOOLCHAIN/include" \
  -c "$LEAN_SRC/src/kernel/type_checker.cpp" -o "$PROBE/type_checker.cpp.o"
clang++ -std=c++20 -O3 -DNDEBUG -DLEAN_EXPORTING -fPIC -fvisibility=hidden \
  -I"$LEAN_SRC/src" -I"$TOOLCHAIN/include" \
  -c "$LEAN_SRC/src/kernel/environment.cpp" -o "$PROBE/environment.cpp.o"
clang++ -std=c++20 -O3 -DNDEBUG -DLEAN_EXPORTING -fPIC -fvisibility=hidden \
  -I"$LEAN_SRC/src" -I"$TOOLCHAIN/include" \
  -c "$LEAN_SRC/src/kernel/equiv_manager.cpp" -o "$PROBE/equiv_manager.cpp.o"
ar -r "$PROBE/libleancpp-instrumented.a" \
  "$PROBE/environment.cpp.o" "$PROBE/type_checker.cpp.o" \
  "$PROBE/equiv_manager.cpp.o"
```

Link the replacement shared library with the toolchain's Clang:

```sh
"$TOOLCHAIN/bin/clang" -dynamiclib -o "$PROBE/libleanshared.dylib" \
  "$PROBE/shell.cpp.o" --sysroot "$TOOLCHAIN" -L"$TOOLCHAIN/lib/libc" \
  -Wl,-force_load,"$PROBE/libleancpp-instrumented.a" \
  -Wl,-force_load,"$TOOLCHAIN/lib/lean/libInit.a" \
  -Wl,-force_load,"$TOOLCHAIN/lib/lean/libStd.a" \
  -Wl,-force_load,"$TOOLCHAIN/lib/lean/libLean.a" \
  -Wl,-force_load,"$TOOLCHAIN/lib/lean/libleanrt.a" \
  -L"$TOOLCHAIN/lib" -lgmp -luv -lssl -lcrypto -lc++ \
  -install_name @rpath/libleanshared.dylib -Wl,-dead_strip
shasum -a 256 "$PROBE/libleanshared.dylib"
```

The recorded dylib SHA-256 is:

```text
f0f19c8af87f6b92ea20ba91f47f9eef18b50e2d20279189ea564e66ae1c8282
```

## Run the opposing controls

Run from Comparator's Lake environment so `Export.Parse` resolves. The
baseline has no diagnostic behavior-changing variable:

```sh
cd "$COMPARATOR"
ELAN_TOOLCHAIN=leanprover/lean4:v4.32.2 lake env \
  env DYLD_LIBRARY_PATH="$PROBE:$TOOLCHAIN/lib/lean" \
  "$TOOLCHAIN/bin/lean" --run "$REPRO/deep-dive/probes/DiagnosticReplay.lean" \
  "$EXPORT"
```

The surgical run adds exactly one variable:

```sh
ELAN_TOOLCHAIN=leanprover/lean4:v4.32.2 lake env \
  env DYLD_LIBRARY_PATH="$PROBE:$TOOLCHAIN/lib/lean" \
  LEAN_PROBE_RESET_EQV_BEFORE_TRANSPORT=1 \
  "$TOOLCHAIN/bin/lean" --run "$REPRO/deep-dive/probes/DiagnosticReplay.lean" \
  "$EXPORT"
```

The expected distinguishing markers are:

```text
baseline: WATCHED_DEFEQ ... initial-cached-hits=22 ... reset-before=0
baseline: target accepted in 4 ms
reset:    WATCHED_DEFEQ ... initial-cached-hits=22 ... reset-before=1
reset:    INFER_FAILURE_PHASE argument-defeq
reset:    INFER_FAILURE_APP App:Eq.mpr/4 ...
reset:    (kernel) deep recursion detected
```

Exact timings are host observations. The outcome, failure site, unfold names,
and zero cardinality-watch counts are the substantive checks.
