#!/usr/bin/env bash

set -euo pipefail

export JAVA_HOME=$(/usr/libexec/java_home -v 21)
export PATH="$JAVA_HOME/bin:$PATH"


export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

STUDY_ROOT="$HOME/gc-explicit-cadence-study"
EXPERIMENT_RUNNER="$HOME/dacapo-gc-study/experiment-runner"
CONFIG="$STUDY_ROOT/configs/services/petclinic/RunnerConfig.py"
RESULT_DIR="$STUDY_ROOT/results/services/petclinic/raw/petclinic_gc_smoke_test"

echo "Removing previous PetClinic smoke-test results..."
rm -rf "$RESULT_DIR"

echo "Running PetClinic experiment..."
cd "$EXPERIMENT_RUNNER"

python experiment-runner/ "$CONFIG"

echo
echo "Done."
echo "Results:"
echo "$RESULT_DIR"