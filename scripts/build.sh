#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ${SURFACE_RESOURCE_SCOPE:-} != build ]]; then
    exec scripts/resource-budget.sh scripts/build.sh "$@"
fi
scripts/bootstrap.sh
mkdir -p artifacts/build
run_stamp="$(date -u +%Y%m%dT%H%M%SZ)-$BASHPID"
build_log="artifacts/build/build-$run_stamp.log"
container_log="artifacts/build/container-$run_stamp.log"
for kind in build container; do
    if [[ -f artifacts/build/$kind.log && ! -L artifacts/build/$kind.log ]]; then
        mv "artifacts/build/$kind.log" "artifacts/build/$kind-before-$run_stamp.log"
    fi
done
ln -sfn "$(basename "$build_log")" artifacts/build/build.log
ln -sfn "$(basename "$container_log")" artifacts/build/container.log
image=localhost/surface-go-builder:bookworm
trap 'podman stop --time 10 surface-go-build >/dev/null 2>&1 || true' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
podman build --force-rm --memory=32g --memory-swap=33g --jobs=1 -t "$image" -f Containerfile . \
    2>&1 | tee "$container_log"
downloads=${SURFACE_DOWNLOADS:-/run/media/system/Files/Downloads/surface-go-build}
args=(run --rm --name surface-go-build --memory=32g --memory-swap=33g
    --cgroup-conf=memory.high=25769803776 --pids-limit=256 --cpus=10
    --cgroup-conf=memory.swap.max=1073741824
    --userns=keep-id --user "$(id -u):$(id -g)" --security-opt label=disable
    -v "$PWD:/work" -v "$downloads:/downloads" -e BR2_DL_DIR=/downloads/buildroot
    -e BR2_JLEVEL=10 -e FORCE_UNSAFE_CONFIGURE=1 "$image")
if [[ ! -f output/.config || ${1:-} == --configure ]]; then
    podman "${args[@]}" make -C buildroot O=/work/output BR2_EXTERNAL=/work/external surface_go1_defconfig
    if [[ ${1:-} == --configure ]]; then shift; fi
fi
podman "${args[@]}" make -C buildroot O=/work/output BR2_EXTERNAL=/work/external -j10 "${@:-all}" \
    2>&1 | tee "$build_log"
if [[ -f output/images/surface-go.img ]]; then
    (cd output/images &&
        if [[ -f surface-go-base.img ]]; then
            sha256sum surface-go.img surface-go-base.img > SHA256SUMS
        else
            sha256sum surface-go.img > SHA256SUMS
        fi)
    podman "${args[@]}" make --no-print-directory -s -C buildroot O=/work/output \
        BR2_EXTERNAL=/work/external show-info > artifacts/build/packages.json
fi
