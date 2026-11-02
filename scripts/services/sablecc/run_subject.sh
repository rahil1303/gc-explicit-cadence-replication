#!/usr/bin/env bash

set -euo pipefail

if [ $# -lt 2 ]; then
    echo "Usage: $0 <gc> <cadence_n>" >&2
    exit 1
fi

GC="$1"
CADENCE_N="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDY_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

SABLECC_HOME="${STUDY_ROOT}/subjects/services/sablecc-3.7"

case "$GC" in
    Serial)
        GC_FLAG="-XX:+UseSerialGC"
        ;;
    Parallel)
        GC_FLAG="-XX:+UseParallelGC"
        ;;
    G1)
        GC_FLAG="-XX:+UseG1GC"
        ;;
    Shenandoah)
        GC_FLAG="-XX:+UseShenandoahGC"
        ;;
    Z|ZGC)
        GC_FLAG="-XX:+UseZGC"
        ;;
    *)
        echo "Error: unknown GC '$GC'" >&2
        exit 1
        ;;
esac

JFR_ARG=""

if [ -n "${JFR_FILE:-}" ]; then
    JFR_ARG="-XX:StartFlightRecording=filename=${JFR_FILE},settings=profile,dumponexit=true"
    echo "JFR recording: ${JFR_FILE}" >&2
fi

cd "$SABLECC_HOME"

echo "Running SableCC gc=${GC} gc.cadence=${CADENCE_N}" >&2
echo "NUMA binding: CPU node 0, memory node 0" >&2

ARGS=()
for _ in $(seq 1 100); do
    ARGS+=("sablecc-3x.sablecc3")
done

# Bind the measured JVM to NUMA node 0 for consistent CPU and memory placement.
# shellcheck disable=SC2086
numactl --cpunodebind=0 --membind=0 \
java $GC_FLAG \
    $JFR_ARG \
    "-Dgc.cadence=${CADENCE_N}" \
    -cp classes \
    org.sablecc.sablecc.SableCC \
    "${ARGS[@]}"

echo "Done." >&2
