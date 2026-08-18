#!/usr/bin/env bash
set -Eeuo pipefail

repo_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
base_image="comppoly-rabin-replay-repro:mechanism-base"
mechanism_image="comppoly-rabin-replay-repro:mechanism"
runner_args=()
build_jobs=4
clean_only=false
build_only=false

usage() {
  cat <<'EOF'
Usage: ./mechanism.sh [--verbose] [--jobs N]
       ./mechanism.sh --build-only [--jobs N]
       ./mechanism.sh --clean

Build and run the opt-in diagnostic kernel experiment. The first build fetches
and compiles exact pinned sources; allow 30 to 40 GiB of free disk space.
The experiment itself runs offline with 8 GiB memory, no swap, four CPUs, an
8 MiB OS main-thread stack limit, and process/time bounds enforced inside the
container. Lean normally dispatches its program main to its own worker stack.

Options:
  --verbose   Print the complete instrumented kernel trace.
  --jobs N    Lean source-build parallelism, from 1 through 4 (default: 4).
  --build-only  Build the two pinned images without running the diagnostic.
  --clean     Remove only this experiment's two Docker images, then exit.
EOF
}

while (($#)); do
  case "$1" in
    --verbose)
      runner_args+=(--verbose)
      shift
      ;;
    --jobs)
      if (($# < 2)); then
        echo "missing value for --jobs" >&2
        usage >&2
        exit 2
      fi
      build_jobs="$2"
      shift 2
      ;;
    --jobs=*)
      build_jobs="${1#*=}"
      shift
      ;;
    --clean)
      clean_only=true
      shift
      ;;
    --build-only)
      build_only=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "$build_jobs" =~ ^[1-4]$ ]]; then
  echo "--jobs must be an integer from 1 through 4" >&2
  exit 2
fi

if [[ "$clean_only" == true ]]; then
  for image in "$mechanism_image" "$base_image"; do
    if docker image inspect "$image" >/dev/null 2>&1; then
      docker image rm "$image"
    fi
  done
  exit 0
fi

# Build the same exact original-suite image used by the unmodified Comparator
# reproduction. Network access exists only during these image-build steps.
docker build \
  --tag "$base_image" \
  --build-arg "LEAN_VERSION=4.32.2" \
  --build-arg "COMPPOLY_REPOSITORY=https://github.com/zksecurity/CompPoly.git" \
  --build-arg "BASE_COMPPOLY_REV=6133f9f796707c438d0a614f97dc218ae976ab8f" \
  --build-arg "TARGET_COMPPOLY_REV=641694629e4557520a1539b272ec338c9f3044c7" \
  --build-arg "BASE_EXPECTATION=pathological" \
  --build-arg "SUITE_NAME=original" \
  --build-arg "COMPPOLY_MANIFEST_SHA256=5f52302efd2c429a7d6cd2f72b26573a6fc09af56f7f956a6037c85e3d10f172" \
  --build-arg "COMPARE_EXPORTS=false" \
  "$repo_dir"

# Build the diagnostic Lean kernel from the pinned Lean source and the exact
# committed instrumentation patch. Docker layer caching makes later runs much
# faster than the first source build.
docker build \
  --file "$repo_dir/deep-dive/Dockerfile.mechanism" \
  --tag "$mechanism_image" \
  --build-arg "BASE_IMAGE=$base_image" \
  --build-arg "LEAN_BUILD_JOBS=$build_jobs" \
  "$repo_dir"

if [[ "$build_only" == true ]]; then
  exit 0
fi

docker_run_args=(
  run
  --rm
  --init
  --network none
  --memory 8g
  --memory-swap 8g
  --cpus 4
  --pids-limit 2048
  --ulimit stack=8388608:8388608
  --security-opt no-new-privileges
  --env ELAN_TOOLCHAIN=leanprover/lean4:v4.32.2
  "$mechanism_image"
)
if ((${#runner_args[@]})); then
  docker_run_args+=("${runner_args[@]}")
fi
docker "${docker_run_args[@]}"
