#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ${SURFACE_RESOURCE_SCOPE:-} != vm ]]; then
    exec scripts/resource-budget.sh --vm scripts/vm-install.sh "$@"
fi
exec python3 scripts/vm.py install "$@"
