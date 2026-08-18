#!/usr/bin/env bash
set -Eeuo pipefail

repo_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mechanism_image="comppoly-rabin-replay-repro:mechanism"
build_jobs=4
confirmed=false
confirmed_large=false
clean_only=false
stop_memory_gib=6

usage() {
  cat <<'EOF'
Usage: ./growth.sh --confirm [--memory-gib 6] [--jobs N]
       ./growth.sh --memory-gib 40 --confirm-large-run [--jobs N]
       ./growth.sh --clean

Run the opt-in guard-bypass growth experiment. It deliberately bypasses Lean's
proactive check_stack guard. By default it stops the exact old target at 6 GiB
under an 8 GiB cgroup cap. The separately confirmed large profile stops at
40 GiB under a 48 GiB cap and requires Docker to report at least 56 GiB total.
Both profiles disable swap and require a cgroup-v2 Docker runtime. The
preceding image build has network access.

The first build compiles pinned Lean source. Allow 30 to 40 GiB of free disk.

Options:
  --confirm   Acknowledge that this intentionally exercises resource growth.
  --memory-gib 6|40
              Select the userspace watchdog target (default: 6).
  --confirm-large-run
              Acknowledge the 40 GiB profile (also implies --confirm).
  --jobs N    Lean source-build parallelism, from 1 through 4 (default: 4).
  --clean     Remove the shared mechanism images, then exit.
EOF
}

while (($#)); do
  case "$1" in
    --confirm)
      confirmed=true
      shift
      ;;
    --confirm-large-run)
      confirmed_large=true
      confirmed=true
      shift
      ;;
    --memory-gib)
      if (($# < 2)); then
        echo "missing value for --memory-gib" >&2
        usage >&2
        exit 2
      fi
      stop_memory_gib="$2"
      shift 2
      ;;
    --memory-gib=*)
      stop_memory_gib="${1#*=}"
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

case "$stop_memory_gib" in
  6)
    growth_profile="bounded"
    container_memory_gib=8
    ;;
  40)
    growth_profile="large-40"
    container_memory_gib=48
    ;;
  *)
    echo "--memory-gib must be exactly 6 or 40" >&2
    exit 2
    ;;
esac

if [[ "$clean_only" == true ]]; then
  exec "$repo_dir/mechanism.sh" --clean
fi

if [[ "$confirmed" != true ]]; then
  echo "refusing to bypass Lean's stack guard without --confirm" >&2
  echo "the live run intentionally grows to ${stop_memory_gib} GiB before its watchdog stops it" >&2
  exit 2
fi

if [[ "$growth_profile" == "large-40" && "$confirmed_large" != true ]]; then
  echo "refusing the 40 GiB profile without --confirm-large-run" >&2
  exit 2
fi
if [[ "$growth_profile" == "bounded" && "$confirmed_large" == true ]]; then
  echo "--confirm-large-run is valid only with --memory-gib 40" >&2
  exit 2
fi

if [[ "$growth_profile" == "large-40" ]]; then
  if ! docker_memory_bytes="$(docker info --format '{{.MemTotal}}')"; then
    echo "cannot query Docker daemon memory" >&2
    exit 2
  fi
  if ! [[ "$docker_memory_bytes" =~ ^[0-9]+$ ]]; then
    echo "Docker reported an invalid memory total: $docker_memory_bytes" >&2
    exit 2
  fi
  minimum_docker_bytes=$((56 * 1024 * 1024 * 1024))
  if ((docker_memory_bytes < minimum_docker_bytes)); then
    echo "the 40 GiB profile requires Docker to report at least 56 GiB total" >&2
    echo "Docker reports $((docker_memory_bytes / 1024 / 1024 / 1024)) GiB" >&2
    exit 2
  fi
fi

echo "WARNING: this run intentionally bypasses Lean's internal stack guard." >&2
echo "The experiment container is capped at ${container_memory_gib} GiB/no swap;" >&2
echo "its runner stops at ${stop_memory_gib} GiB." >&2
if [[ "$growth_profile" == "large-40" ]]; then
  echo "LARGE RUN: reserve the machine for this experiment and close other workloads." >&2
fi
echo "The preceding image build is networked and outside that runtime cgroup." >&2

"$repo_dir/mechanism.sh" --build-only --jobs "$build_jobs"

container_owner="${growth_profile}-$$-$(date +%s)"
container_name="comppoly-rabin-replay-growth-${container_owner}"
cleanup_container() {
  local actual_owner
  actual_owner="$(docker container inspect \
    --format '{{ index .Config.Labels "org.zksecurity.comppoly-repro.owner" }}' \
    "$container_name" 2>/dev/null || true)"
  if [[ "$actual_owner" == "$container_owner" ]]; then
    docker container rm --force "$container_name" >/dev/null 2>&1 || true
  fi
}
trap cleanup_container EXIT

docker run \
  --rm \
  --name "$container_name" \
  --label "org.zksecurity.comppoly-repro.owner=$container_owner" \
  --init \
  --cgroupns private \
  --network none \
  --memory "${container_memory_gib}g" \
  --memory-swap "${container_memory_gib}g" \
  --cpus 4 \
  --pids-limit 512 \
  --ulimit stack=-1:-1 \
  --security-opt no-new-privileges \
  --env ELAN_TOOLCHAIN=leanprover/lean4:v4.32.2 \
  --entrypoint python3 \
  "$mechanism_image" \
  /repro/deep-dive/run_growth.py \
  --profile "$growth_profile"

trap - EXIT
