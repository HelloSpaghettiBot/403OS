#!/usr/bin/env bash
set -euo pipefail
mode=build
high=24G
maximum=32G
workers=10
if [[ ${1:-} == --vm ]]; then
    shift
    mode=vm
    high=5G
    maximum=6G
    workers=2
fi
[[ $# -gt 0 ]] || { echo 'Supply a command.' >&2; exit 2; }
command -v systemd-run >/dev/null
command -v flock >/dev/null
runtime=${XDG_RUNTIME_DIR:?A user systemd session is required}
exec flock --nonblock --conflict-exit-code 75 "$runtime/codex-heavy-build.lock" \
    systemd-run --user --scope --quiet --collect \
    --unit="surface-go-${mode}-${BASHPID}" \
    -p MemoryAccounting=yes -p "MemoryHigh=$high" -p "MemoryMax=$maximum" \
    -p MemorySwapMax=1G -p TasksMax=256 \
    env SURFACE_RESOURCE_SCOPE="$mode" MAKEFLAGS="-j$workers" CMAKE_BUILD_PARALLEL_LEVEL="$workers" "$@"
