#!/usr/bin/env bash

set -euo pipefail

export JAVA_HOME=$(/usr/libexec/java_home -v 21)
export PATH="$JAVA_HOME/bin:$PATH"

STUDY_ROOT="$HOME/gc-explicit-cadence-study"
EXPERIMENT_RUNNER="$HOME/dacapo-gc-study/experiment-runner"
CONFIG="$STUDY_ROOT/configs/services/sablecc/RunnerConfig.py"
RESULT_DIR="$STUDY_ROOT/results/services/sablecc/raw/sablecc_gc_smoke_test"

echo "Removing previous SableCC smoke-test results..."
rm -rf "$RESULT_DIR"

echo "Running SableCC experiment..."
cd "$EXPERIMENT_RUNNER"

python experiment-runner/ "$CONFIG"

echo
echo "Done."
echo "Results:"
echo "$RESULT_DIR"
