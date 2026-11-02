#!/usr/bin/env bash

set -euo pipefail

if [ $# -lt 3 ]; then
    echo "Usage: $0 <subject> <gc> <cadence_n> [extra harness args...]" >&2
    exit 1
fi

SUBJECT="$1"
GC="$2"
CADENCE_N="$3"
shift 3

EXTRA_ARGS=()
if [ "$#" -gt 0 ]; then
    EXTRA_ARGS=("$@")
fi

# Resolve study paths independently of the current working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDY_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

DACAPO_ROOT="${STUDY_ROOT}/subjects/dacapo"
DACAPO_HOME="${DACAPO_ROOT}/dacapo-23.11-MR2-chopin"
CHOPIN_JAR="${DACAPO_ROOT}/dacapo-23.11-MR2-chopin.jar"
CALLBACK_DIR="${STUDY_ROOT}/instrumentation"

LAUNCHER="${DACAPO_HOME}/launchers/${SUBJECT}.jar"

if [ ! -f "$LAUNCHER" ]; then
    echo "Error: launcher not found: $LAUNCHER" >&2
    exit 1
fi

if [ ! -f "$CHOPIN_JAR" ]; then
    echo "Error: DaCapo jar not found: $CHOPIN_JAR" >&2
    exit 1
fi

if [ ! -f "${CALLBACK_DIR}/CadenceCallback.class" ]; then
    echo "Error: CadenceCallback.class not found in ${CALLBACK_DIR}" >&2
    exit 1
fi

# Read and unwrap Class-Path from subject launcher manifest.
MANIFEST_CP=$(unzip -p "$LAUNCHER" META-INF/MANIFEST.MF \
    | awk '
        /^Class-Path:/ {
            sub(/^Class-Path: /, "");
            buf=$0;
            incp=1;
            next
        }
        incp && /^ / {
            sub(/^ /, "");
            buf=buf $0;
            next
        }
        incp {
            print buf;
            incp=0
        }
        END {
            if (incp) print buf
        }
    ')

if [ -z "$MANIFEST_CP" ]; then
    echo "Error: could not read Class-Path from $LAUNCHER manifest" >&2
    exit 1
fi

# Callback directory and DaCapo harness jar must be on the classpath.
CP="${CALLBACK_DIR}:${CHOPIN_JAR}"

for entry in $MANIFEST_CP; do
    CP="${CP}:${DACAPO_HOME}/launchers/${entry}"
done

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
    Default|default)
        GC_FLAG=""
        ;;
    *)
        echo "Error: unknown GC '$GC'" >&2
        exit 1
        ;;
esac

LOG_FILE="gc-${SUBJECT}-${GC}-n${CADENCE_N}.log"

# Optional JFR recording.
# Experiment Runner supplies JFR_FILE for production runs.
JFR_ARG=""

if [ -n "${JFR_FILE:-}" ]; then
    JFR_ARG="-XX:StartFlightRecording=filename=${JFR_FILE},settings=profile,dumponexit=true"
    echo "JFR recording: ${JFR_FILE}" >&2
fi

echo "Running subject=${SUBJECT} gc=${GC} cadence=${CADENCE_N}" >&2
echo "NUMA binding: CPU node 0, memory node 0" >&2

# DaCapo expects its dat/, jar/, and launcher assets relative to its home.
cd "$DACAPO_HOME"

# Bind the measured JVM to NUMA node 0 for consistent CPU and memory placement.
# shellcheck disable=SC2086
numactl --cpunodebind=0 --membind=0 \
java $GC_FLAG \
    $JFR_ARG \
    "-Xlog:gc*:file=${LOG_FILE}" \
    "-Dcadence.n=${CADENCE_N}" \
    -cp "$CP" \
    Harness \
    -c CadenceCallback \
    --no-pre-iteration-gc \
    ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} \
    "$SUBJECT"

echo "Done. GC log: ${DACAPO_HOME}/${LOG_FILE}" >&2

if [ -n "${JFR_FILE:-}" ]; then
    echo "Done. JFR recording: ${JFR_FILE}" >&2
fi
