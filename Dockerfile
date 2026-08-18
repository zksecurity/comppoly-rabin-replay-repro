# syntax=docker/dockerfile:1@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89

ARG UBUNTU_IMAGE=ubuntu:24.04@sha256:4fbb8e6a8395de5a7550b33509421a2bafbc0aab6c06ba2cef9ebffbc7092d90
FROM ${UBUNTU_IMAGE}

ARG TARGETARCH
ARG LEAN_VERSION=4.32.2
ARG ELAN_VERSION=4.2.3
ARG COMPARATOR_REV=51491237b1d2f96cca203af9c34bced6fe38e0d8
ARG LEAN4EXPORT_REV=af5aa64bb914c3c2c781f378088dbd38acf4f804
ARG COMPPOLY_REPOSITORY=https://github.com/zksecurity/CompPoly.git
ARG BASE_COMPPOLY_REV=6133f9f796707c438d0a614f97dc218ae976ab8f
ARG TARGET_COMPPOLY_REV=641694629e4557520a1539b272ec338c9f3044c7
ARG BASE_EXPECTATION=pathological
ARG SUITE_NAME=original
ARG COMPPOLY_MANIFEST_SHA256=5f52302efd2c429a7d6cd2f72b26573a6fc09af56f7f956a6037c85e3d10f172
ARG COMPARE_EXPORTS=false

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git python3 zstd \
    && rm -rf /var/lib/apt/lists/* \
    && if getent passwd 1000 >/dev/null; then userdel -r "$(getent passwd 1000 | cut -d: -f1)"; fi \
    && if getent group 1000 >/dev/null; then groupdel "$(getent group 1000 | cut -d: -f1)"; fi \
    && groupadd --gid 1000 runner \
    && useradd --create-home --uid 1000 --gid 1000 --shell /bin/bash runner

USER runner:runner
ENV HOME=/home/runner
ENV PATH=/home/runner/.elan/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /home/runner

RUN set -eu; \
    case "${TARGETARCH}" in \
      amd64) \
        elan_target=x86_64-unknown-linux-gnu; \
        elan_sha=df0b2b3a439961ffcbb3985214365ffe40f49bc871df04dff268c7d8e21ca8b2 ;; \
      arm64) \
        elan_target=aarch64-unknown-linux-gnu; \
        elan_sha=cb69af0803b04157bc30201c29c12fca882bb3ad8b43476b8d2d3064810bc3ac ;; \
      *) echo "unsupported target architecture: ${TARGETARCH}" >&2; exit 2 ;; \
    esac; \
    curl --fail --show-error --location \
      --output /tmp/elan.tar.gz \
      "https://github.com/leanprover/elan/releases/download/v${ELAN_VERSION}/elan-${elan_target}.tar.gz"; \
    echo "${elan_sha}  /tmp/elan.tar.gz" | sha256sum --check --strict; \
    tar -xzf /tmp/elan.tar.gz -C /tmp; \
    /tmp/elan-init -y --no-modify-path --default-toolchain none; \
    rm -f /tmp/elan-init /tmp/elan.tar.gz; \
    elan toolchain install "leanprover/lean4:v${LEAN_VERSION}"; \
    elan default "leanprover/lean4:v${LEAN_VERSION}"; \
    test "$(lean --version | sed -n 's/^Lean (version \([^,]*\),.*/\1/p')" = "${LEAN_VERSION}"

# Build the exact Comparator source and its committed lean4export dependency
# under the same Lean version as the two CompPoly revisions.
RUN set -eu; \
    git init /home/runner/comparator; \
    git -C /home/runner/comparator remote add origin https://github.com/leanprover/comparator.git; \
    git -C /home/runner/comparator fetch --no-tags --depth=1 origin "${COMPARATOR_REV}"; \
    git -C /home/runner/comparator checkout --detach FETCH_HEAD; \
    test "$(git -C /home/runner/comparator rev-parse HEAD)" = "${COMPARATOR_REV}"; \
    test -z "$(git -C /home/runner/comparator status --porcelain=v1 --untracked-files=all)"; \
    cd /home/runner/comparator; \
    ELAN_TOOLCHAIN="leanprover/lean4:v${LEAN_VERSION}" lake build comparator lean4export; \
    test "$(git -C .lake/packages/lean4export rev-parse HEAD)" = "${LEAN4EXPORT_REV}"; \
    test -z "$(git -C .lake/packages/lean4export status --porcelain=v1 --untracked-files=no)"; \
    changed="$(git diff --name-only)"; \
    test -z "${changed}" || test "${changed}" = "lake-manifest.json"; \
    committed_manifest="$(git show HEAD:lake-manifest.json)"; \
    normalized_manifest="$(printf '%s\n' "${committed_manifest}" \
      | sed 's#https://github.com/leanprover/lean4export"#https://github.com/leanprover/lean4export.git"#')"; \
    actual_manifest="$(sed -n '1,$p' lake-manifest.json)"; \
    test "${actual_manifest}" = "${committed_manifest}" \
      || test "${actual_manifest}" = "${normalized_manifest}"; \
    git restore lake-manifest.json; \
    test -z "$(git status --porcelain=v1 --untracked-files=all --ignore-submodules=none)"

# Fetch both immutable CompPoly revisions and populate their shared Mathlib
# cache while the image build still has network access. Each suite requires its
# two revisions to pin the same Lean/Lake dependency stack. The actual
# experiment is run later with networking disabled.
RUN set -eu; \
    git init /home/runner/CompPoly; \
    git -C /home/runner/CompPoly remote add origin "${COMPPOLY_REPOSITORY}"; \
    git -C /home/runner/CompPoly fetch --no-tags --depth=1 origin "${BASE_COMPPOLY_REV}"; \
    git -C /home/runner/CompPoly fetch --no-tags --depth=1 origin "${TARGET_COMPPOLY_REV}"; \
    test "$(git -C /home/runner/CompPoly cat-file commit "${TARGET_COMPPOLY_REV}" \
      | sed -n 's/^parent //p')" = "${BASE_COMPPOLY_REV}"; \
    git -C /home/runner/CompPoly diff --quiet \
      "${BASE_COMPPOLY_REV}" "${TARGET_COMPPOLY_REV}" -- \
      lean-toolchain lakefile.lean lake-manifest.json; \
    test "$(git -C /home/runner/CompPoly show "${TARGET_COMPPOLY_REV}:lean-toolchain")" \
      = "leanprover/lean4:v${LEAN_VERSION}"; \
    test "$(git -C /home/runner/CompPoly show "${BASE_COMPPOLY_REV}:lean-toolchain")" \
      = "leanprover/lean4:v${LEAN_VERSION}"; \
    git -C /home/runner/CompPoly checkout --detach "${TARGET_COMPPOLY_REV}"; \
    test "$(git -C /home/runner/CompPoly rev-parse HEAD)" = "${TARGET_COMPPOLY_REV}"; \
    cd /home/runner/CompPoly; \
    echo "${COMPPOLY_MANIFEST_SHA256}  lake-manifest.json" \
      | sha256sum --check --strict; \
    lake exe cache get; \
    lake clean CompPoly; \
    test -z "$(git status --porcelain=v1 --untracked-files=all --ignore-submodules=none)"

WORKDIR /repro
COPY --chown=runner:runner case /repro/case
COPY --chown=runner:runner scripts /repro/scripts

ENV REPRO_SUITE_NAME=${SUITE_NAME} \
    REPRO_LEAN_VERSION=${LEAN_VERSION} \
    REPRO_COMPARATOR_REV=${COMPARATOR_REV} \
    REPRO_LEAN4EXPORT_REV=${LEAN4EXPORT_REV} \
    REPRO_BASE_COMPPOLY_REV=${BASE_COMPPOLY_REV} \
    REPRO_TARGET_COMPPOLY_REV=${TARGET_COMPPOLY_REV} \
    REPRO_BASE_EXPECTATION=${BASE_EXPECTATION} \
    REPRO_COMPARE_EXPORTS=${COMPARE_EXPORTS}

ENTRYPOINT ["python3", "/repro/scripts/run_repro.py"]
