#!/usr/bin/env bash
set -euo pipefail

# Fetch one completed remote run directory from the Linux measurement server
# back to the Raspberry Pi.
#
# Usage:
#   ./fetch_run_artifacts.sh <run_id> [local_destination]
#
# Examples:
#   ./fetch_run_artifacts.sh h2__G1__cadence-6__rep-03
#
#   ./fetch_run_artifacts.sh \
#       h2__G1__cadence-6__rep-03 \
#       /home/pi/gc-explicit-cadence-study/results/dacapo/raw/run_0003
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
# If local_destination is omitted, the run is copied to:
#   <local study root>/results/remote/<run_id>

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    echo "Usage: $0 <run_id> [local_destination]" >&2
    exit 1
fi

RUN_ID="$1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_STUDY_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

: "${GC_SERVER_HOST:?Set GC_SERVER_HOST to the measurement server hostname or IP}"
: "${GC_SERVER_USER:?Set GC_SERVER_USER to the SSH username on the measurement server}"

GC_SERVER_PORT="${GC_SERVER_PORT:-22}"
GC_SERVER_STUDY_ROOT="${GC_SERVER_STUDY_ROOT:-$HOME/gc-explicit-cadence-study}"
GC_REMOTE_RESULTS_SUBDIR="${GC_REMOTE_RESULTS_SUBDIR:-results/remote}"

if [[ ! "$RUN_ID" =~ ^[A-Za-z0-9._-]+$ ]]; then
    echo "Error: run_id may contain only letters, numbers, '.', '_' and '-': $RUN_ID" >&2
    exit 1
fi

REMOTE_RUN_DIR="${GC_SERVER_STUDY_ROOT}/${GC_REMOTE_RESULTS_SUBDIR}/${RUN_ID}"

if [ "$#" -eq 2 ]; then
    LOCAL_DEST="$2"
else
    LOCAL_DEST="${LOCAL_STUDY_ROOT}/${GC_REMOTE_RESULTS_SUBDIR}/${RUN_ID}"
fi

# Refuse to overwrite a previous local result.
if [ -e "$LOCAL_DEST" ]; then
    echo "Error: local destination already exists: $LOCAL_DEST" >&2
    exit 2
fi

# Verify the remote run exists and has metadata before copying.
printf -v Q_REMOTE_RUN_DIR '%q' "$REMOTE_RUN_DIR"

ssh \
    -p "$GC_SERVER_PORT" \
    -o BatchMode=yes \
    "${GC_SERVER_USER}@${GC_SERVER_HOST}" \
    "test -d ${Q_REMOTE_RUN_DIR} && test -f ${Q_REMOTE_RUN_DIR}/metadata.env"

mkdir -p "$(dirname "$LOCAL_DEST")"

echo "Fetching remote run: ${RUN_ID}"
echo "From: ${GC_SERVER_USER}@${GC_SERVER_HOST}:${REMOTE_RUN_DIR}"
echo "To:   ${LOCAL_DEST}"

# Prefer rsync because it is resumable and preserves timestamps.
if command -v rsync >/dev/null 2>&1; then
    mkdir -p "$LOCAL_DEST"
    rsync \
        -a \
        --partial \
        -e "ssh -p ${GC_SERVER_PORT} -o BatchMode=yes" \
        "${GC_SERVER_USER}@${GC_SERVER_HOST}:${REMOTE_RUN_DIR}/" \
        "${LOCAL_DEST}/"
else
    # scp fallback if rsync is unavailable on the Pi.
    scp \
        -P "$GC_SERVER_PORT" \
        -o BatchMode=yes \
        -r \
        "${GC_SERVER_USER}@${GC_SERVER_HOST}:${REMOTE_RUN_DIR}" \
        "$LOCAL_DEST"
fi

test -f "${LOCAL_DEST}/metadata.env" || {
    echo "Error: fetch completed but metadata.env is missing locally." >&2
    exit 3
}

echo "Fetch complete."
