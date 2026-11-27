#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ${SURFACE_RESOURCE_SCOPE:-} != build ]]; then
    exec scripts/resource-budget.sh scripts/build-iso.sh "$@"
fi
image=localhost/surface-go-builder:bookworm
podman build --force-rm --memory=32g --memory-swap=33g --jobs=1 -t "$image" -f Containerfile .
exec podman run --rm --name surface-go-iso --memory=32g --memory-swap=33g \
    --cgroup-conf=memory.high=25769803776 --cgroup-conf=memory.swap.max=1073741824 \
    --pids-limit=256 --cpus=10 --userns=keep-id --user "$(id -u):$(id -g)" \
    --security-opt label=disable -v "$PWD:/work" -e SURFACE_RESOURCE_SCOPE=build \
    "$image" python3 scripts/build-iso.py "$@"
