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

TRAIN_TICKET_HOME="${STUDY_ROOT}/subjects/services/train-ticket"
USER_SERVICE_JAR="${TRAIN_TICKET_HOME}/ts-user-service/target/ts-user-service-1.0.jar"

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

if [ ! -f "$USER_SERVICE_JAR" ]; then
    echo "Error: Train Ticket user-service jar not found: $USER_SERVICE_JAR" >&2
    exit 1
fi

export USER_MYSQL_HOST=127.0.0.1
export USER_MYSQL_PORT=3306
export USER_MYSQL_DATABASE=ts-user-mysql
export USER_MYSQL_USER=tsuser
export USER_MYSQL_PASSWORD='notTheStrongestPassword'

export SPRING_CLOUD_NACOS_DISCOVERY_ENABLED=false
export SPRING_CLOUD_SERVICE_REGISTRY_AUTO_REGISTRATION_ENABLED=false

echo "Starting Train Ticket user-service gc=${GC} gc.cadence=${CADENCE_N}" >&2
echo "NUMA binding: CPU node 0, memory node 0" >&2

echo "USER_MYSQL_HOST=$USER_MYSQL_HOST" >&2
echo "USER_MYSQL_PORT=$USER_MYSQL_PORT" >&2
echo "USER_MYSQL_DATABASE=$USER_MYSQL_DATABASE" >&2
echo "USER_MYSQL_USER=$USER_MYSQL_USER" >&2


cd "$TRAIN_TICKET_HOME"

# Bind the measured JVM to NUMA node 0 for consistent CPU and memory placement.
# shellcheck disable=SC2086
exec numactl --cpunodebind=0 --membind=0 \
java $GC_FLAG \
    "-Dgc.cadence=${CADENCE_N}" \
    -jar "$USER_SERVICE_JAR"
