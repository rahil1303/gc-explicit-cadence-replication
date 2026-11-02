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

PETCLINIC_HOME="${STUDY_ROOT}/subjects/services/spring-petclinic-rest"
PETCLINIC_JAR="${PETCLINIC_HOME}/target/spring-petclinic-rest-4.0.2.jar"

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

if [ ! -f "$PETCLINIC_JAR" ]; then
    echo "Error: PetClinic jar not found: $PETCLINIC_JAR" >&2
    exit 1
fi

JFR_ARG=""

if [ -n "${JFR_FILE:-}" ]; then
    JFR_ARG="-XX:StartFlightRecording=filename=${JFR_FILE},settings=profile,dumponexit=true"
    echo "JFR recording: ${JFR_FILE}" >&2
fi

echo "Starting PetClinic gc=${GC} gc.cadence=${CADENCE_N}" >&2
echo "NUMA binding: CPU node 0, memory node 0" >&2

# Bind the measured JVM to NUMA node 0 for consistent CPU and memory placement.
# shellcheck disable=SC2086
exec numactl --cpunodebind=0 --membind=0 \
java $GC_FLAG \
    $JFR_ARG \
    "-Dgc.cadence=${CADENCE_N}" \
    -jar "$PETCLINIC_JAR"
