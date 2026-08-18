#!/usr/bin/env bash
set -Eeuo pipefail

# `lake env` prepends the official toolchain libraries. Remove that search path
# so the patched stage1 executable resolves the libraries in its own RUNPATH.
unset LD_LIBRARY_PATH
exec /home/runner/lean4-instrumented/build/release/stage1/bin/lean "$@"
