#!/usr/bin/env bash
set -Eeuo pipefail

repo_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
suite="original"
runner_args=()

usage() {
  cat <<'EOF'
Usage: ./repro.sh [--suite original|followup|all] [runner options]

Suites:
  original  Lean 4.32.2: old rw proof versus explicit-q wrapper (default)
  followup  Lean 4.33.0: wrapper parent versus implicit-q/docs follow-up
  all       Run both independently pinned suites

Runner options are passed into the container:
  --mode target|both
  --timeout SECONDS       Positive and at most 90 (default: 90)
EOF
}

while (($#)); do
  case "$1" in
    --suite)
      if (($# < 2)); then
        echo "missing value for --suite" >&2
        usage >&2
        exit 2
      fi
      suite="$2"
      shift 2
      ;;
    --suite=*)
      suite="${1#*=}"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      runner_args+=("$1")
      shift
      ;;
  esac
done

run_suite() {
  local selected="$1"
  local image_tag="comppoly-rabin-replay-repro:${selected}"
  local lean_version
  local repository
  local base_revision
  local target_revision
  local base_expectation
  local manifest_sha256
  local compare_exports

  case "$selected" in
    original)
      lean_version="4.32.2"
      repository="https://github.com/zksecurity/CompPoly.git"
      base_revision="6133f9f796707c438d0a614f97dc218ae976ab8f"
      target_revision="641694629e4557520a1539b272ec338c9f3044c7"
      base_expectation="pathological"
      manifest_sha256="5f52302efd2c429a7d6cd2f72b26573a6fc09af56f7f956a6037c85e3d10f172"
      compare_exports="false"
      ;;
    followup)
      lean_version="4.33.0"
      repository="https://github.com/Verified-zkEVM/CompPoly.git"
      base_revision="32a0c29e41225e8cec2a2e1eab1dfab64f026aa0"
      target_revision="7480a691ff87d178f0d0afd45454d8400e39e268"
      base_expectation="accepted"
      manifest_sha256="b5949e1921bbebc466b7fe23aa266bd43fa2a84170c9999b6f002b020af2de4a"
      compare_exports="true"
      ;;
    *)
      echo "unknown suite: $selected" >&2
      usage >&2
      exit 2
      ;;
  esac

  docker build \
    --tag "$image_tag" \
    --build-arg "LEAN_VERSION=$lean_version" \
    --build-arg "COMPPOLY_REPOSITORY=$repository" \
    --build-arg "BASE_COMPPOLY_REV=$base_revision" \
    --build-arg "TARGET_COMPPOLY_REV=$target_revision" \
    --build-arg "BASE_EXPECTATION=$base_expectation" \
    --build-arg "SUITE_NAME=$selected" \
    --build-arg "COMPPOLY_MANIFEST_SHA256=$manifest_sha256" \
    --build-arg "COMPARE_EXPORTS=$compare_exports" \
    "$repo_dir"

  local docker_run_args=(
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
    "$image_tag"
  )
  if ((${#runner_args[@]})); then
    docker_run_args+=("${runner_args[@]}")
  fi
  docker "${docker_run_args[@]}"
}

case "$suite" in
  original|followup)
    run_suite "$suite"
    ;;
  all)
    run_suite followup
    run_suite original
    ;;
  *)
    echo "unknown suite: $suite" >&2
    usage >&2
    exit 2
    ;;
esac
