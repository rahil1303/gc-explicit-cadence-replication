#!/usr/bin/env bash

set -euo pipefail

export JAVA_HOME=$(/usr/libexec/java_home -v 21)
export PATH="$JAVA_HOME/bin:$PATH"

# Required for the local macOS Experiment Runner fork behaviour.
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

STUDY_ROOT="$HOME/gc-explicit-cadence-study"
EXPERIMENT_RUNNER="$HOME/dacapo-gc-study/experiment-runner"
CONFIG="$STUDY_ROOT/configs/services/train-ticket/RunnerConfig.py"

RESULT_DIR="$STUDY_ROOT/results/services/train-ticket/raw/train_ticket_gc_smoke_test"

echo "Removing previous Train Ticket smoke-test results..."
rm -rf "$RESULT_DIR"

echo "Running Train Ticket experiment..."

cd "$EXPERIMENT_RUNNER"

python experiment-runner/ "$CONFIG"

echo
echo "Done."
echo "Results:"
echo "$RESULT_DIR"