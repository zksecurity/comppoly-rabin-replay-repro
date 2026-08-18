#!/usr/bin/env bash
set -Eeuo pipefail

repo_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$repo_dir/deep-dive/check_small_exponent.py" "$@"
