#!/usr/bin/env bash
set -euo pipefail

# Launch one measured-study command on the Linux measurement server.
#
# This script is intended to be called FROM THE RASPBERRY PI.
# The same gc-explicit-cadence-study repository should exist on both machines.
#
# Usage:
#   ./run_remote_subject.sh <run_id> <server-command> [args...]
#
# Example:
#   ./run_remote_subject.sh \
#       h2__G1__cadence-6__rep-03 \
#       scripts/dacapo/run_subject.sh \
#       h2 G1 6 -n 30 -s large
#
# Required environment variables:
#   GC_SERVER_HOST
#   GC_SERVER_USER
#
# Optional:
#   GC_SERVER_PORT=22
#   GC_SERVER_STUDY_ROOT=$HOME/gc-explicit-cadence-study
#   GC_REMOTE_RESULTS_SUBDIR=results/remote
#
# On the server this script creates:
#   results/remote/<run_id>/
#
# and exports:
#   GC_REMOTE_RUN_ID
#   GC_REMOTE_RUN_DIR
#   JFR_FILE
#   GC_LOG_FILE
#   STDOUT_LOG
#   STDERR_LOG
#
# The remote command may use those variables.

if [ "$#" -lt 2 ]; then
    echo "Usage: $0 <run_id> <server-command> [args...]" >&2
    exit 1
fi

RUN_ID="$1"
shift

REMOTE_COMMAND="$1"
shift

: "${GC_SERVER_HOST:?Set GC_SERVER_HOST to the measurement server hostname or IP}"
: "${GC_SERVER_USER:?Set GC_SERVER_USER to the SSH username on the measurement server}"

GC_SERVER_PORT="${GC_SERVER_PORT:-22}"
GC_SERVER_STUDY_ROOT="${GC_SERVER_STUDY_ROOT:-$HOME/gc-explicit-cadence-study}"
GC_REMOTE_RESULTS_SUBDIR="${GC_REMOTE_RESULTS_SUBDIR:-results/remote}"

# Keep run IDs safe for use as directory names.
if [[ ! "$RUN_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "Error: run_id may contain only letters, numbers, '.', '_' and '-': $RUN_ID" >&2
    exit 1
fi

# Quote all positional arguments for safe transport through SSH.
printf -v REMOTE_ARGS ' %q' "$@"
printf -v Q_STUDY_ROOT '%q' "$GC_SERVER_STUDY_ROOT"
printf -v Q_RESULTS_SUBDIR '%q' "$GC_REMOTE_RESULTS_SUBDIR"
printf -v Q_RUN_ID '%q' "$RUN_ID"
printf -v Q_COMMAND '%q' "$REMOTE_COMMAND"

echo "Remote run: ${RUN_ID}"
echo "Server: ${GC_SERVER_USER}@${GC_SERVER_HOST}:${GC_SERVER_PORT}"
echo "Command: ${REMOTE_COMMAND}${REMOTE_ARGS}"

ssh \
    -p "$GC_SERVER_PORT" \
    -o BatchMode=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    "${GC_SERVER_USER}@${GC_SERVER_HOST}" \
    "
set -euo pipefail

STUDY_ROOT=${Q_STUDY_ROOT}
RESULTS_SUBDIR=${Q_RESULTS_SUBDIR}
RUN_ID=${Q_RUN_ID}

cd \"\$STUDY_ROOT\"

RUN_DIR=\"\$STUDY_ROOT/\$RESULTS_SUBDIR/\$RUN_ID\"

if [ -e \"\$RUN_DIR\" ]; then
    echo \"Error: remote run directory already exists: \$RUN_DIR\" >&2
    exit 2
fi

mkdir -p \"\$RUN_DIR\"

export GC_REMOTE_RUN_ID=\"\$RUN_ID\"
export GC_REMOTE_RUN_DIR=\"\$RUN_DIR\"
export JFR_FILE=\"\$RUN_DIR/recording.jfr\"
export GC_LOG_FILE=\"\$RUN_DIR/gc.log\"
export STDOUT_LOG=\"\$RUN_DIR/stdout.log\"
export STDERR_LOG=\"\$RUN_DIR/stderr.log\"

cat > \"\$RUN_DIR/metadata.env\" <<EOF
run_id=\$RUN_ID
host=\$(hostname)
study_root=\$STUDY_ROOT
started_at_utc=\$(date -u +%Y-%m-%dT%H:%M:%SZ)
command=${Q_COMMAND}${REMOTE_ARGS}
status=running
EOF

set +e
${Q_COMMAND}${REMOTE_ARGS} \
    > >(tee \"\$STDOUT_LOG\") \
    2> >(tee \"\$STDERR_LOG\" >&2)
STATUS=\$?
set -e

{
    echo \"finished_at_utc=\$(date -u +%Y-%m-%dT%H:%M:%SZ)\"
    echo \"exit_code=\$STATUS\"
    if [ \"\$STATUS\" -eq 0 ]; then
        echo \"status=complete\"
    else
        echo \"status=failed\"
    fi
} >> \"\$RUN_DIR/metadata.env\"

exit \"\$STATUS\"
"
