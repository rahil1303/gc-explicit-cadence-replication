#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <server-command> [args...]" >&2
    echo "Example: $0 scripts/dacapo/run_subject.sh h2 G1 15 -n 30 -s large" >&2
    exit 1
fi

: "${GC_SERVER_HOST:?Set GC_SERVER_HOST to the measurement server hostname or IP}"
: "${GC_SERVER_USER:?Set GC_SERVER_USER to the SSH username on the measurement server}"

GC_SERVER_PORT="${GC_SERVER_PORT:-22}"
GC_SERVER_STUDY_ROOT="${GC_SERVER_STUDY_ROOT:-$HOME/gc-explicit-cadence-study}"

REMOTE_COMMAND="$1"
shift

printf -v REMOTE_ARGS ' %q' "$@"
printf -v REMOTE_ROOT '%q' "$GC_SERVER_STUDY_ROOT"
printf -v REMOTE_CMD '%q' "$REMOTE_COMMAND"

ssh \
    -p "$GC_SERVER_PORT" \
    -o BatchMode=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    "${GC_SERVER_USER}@${GC_SERVER_HOST}" \
    "cd ${REMOTE_ROOT} && ${REMOTE_CMD}${REMOTE_ARGS}"
