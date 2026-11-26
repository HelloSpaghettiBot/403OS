#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/tests artifacts/tests
g++ -std=c++20 -Wall -Wextra -Wpedantic -Werror -Iui/include \
    ui/tests/global_home_tests.cpp ui/src/global_home.cpp -o build/tests/global-home-tests
build/tests/global-home-tests | tee artifacts/tests/global-home.log
g++ -std=c++20 -Wall -Wextra -Wpedantic -Werror -pthread -Iui/include \
    ui/tests/core_tests.cpp ui/src/model.cpp ui/src/status.cpp ui/src/process.cpp ui/src/library.cpp ui/src/progress.cpp \
    -o build/tests/core-tests
build/tests/core-tests | tee artifacts/tests/core.log
g++ -std=c++20 -Wall -Wextra -Wpedantic -Werror -pthread -Iui/include \
    ui/tests/status_tests.cpp ui/src/status.cpp -o build/tests/status-tests
build/tests/status-tests | tee artifacts/tests/status.log
g++ -std=c++20 -Wall -Wextra -Wpedantic -Werror -pthread -Iui/include \
    ui/tests/control_center_tests.cpp ui/src/control_center.cpp ui/src/model.cpp -o build/tests/control-center-tests
build/tests/control-center-tests | tee artifacts/tests/control-center.log
g++ -std=c++20 -Wall -Wextra -Wpedantic -Werror -pthread -Iui/include \
    ui/tests/control_center_json_tests.cpp ui/src/model.cpp -o build/tests/command-json-tests
build/tests/command-json-tests | tee artifacts/tests/command-json.log
g++ -std=c++20 -Wall -Wextra -Wpedantic -Werror -Iui/include ui/tests/persistent_memory_tests.cpp -o build/tests/persistent-memory-tests
build/tests/persistent-memory-tests | tee artifacts/tests/persistent-memory.log
python3 -m unittest discover -s tests -v 2>&1 | tee artifacts/tests/scripts.log
python3 -m unittest discover -s services/pokemmo-setup -p 'test_*.py' -v \
    2>&1 | tee artifacts/tests/pokemmo-setup.log
python3 -m unittest discover -s services/dw3 -p 'test_*.py' -v \
    2>&1 | tee artifacts/tests/dw3-setup.log
while IFS= read -r script; do bash -n "$script"; done < <(find scripts -name '*.sh')
while IFS= read -r script; do sh -n "$script"; done < <(find external/board installer -type f \( -name '*.sh' -o -name 'S[0-9]*' -o -name surface-session \))
