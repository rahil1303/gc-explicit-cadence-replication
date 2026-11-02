#!/usr/bin/env bash
set -euo pipefail

echo "=== Java ==="
java -version

echo
echo "=== JFR / jcmd ==="
command -v jfr
command -v jcmd

echo
echo "=== EnergiBridge ==="
command -v energibridge || command -v /usr/local/bin/energibridge

echo
echo "=== Collector availability ==="

check_gc() {
    local name="$1"
    local flag="$2"

    if java "$flag" -version >/dev/null 2>&1; then
        echo "OK   $name"
    else
        echo "FAIL $name"
        return 1
    fi
}

check_gc "Serial" "-XX:+UseSerialGC"
check_gc "Parallel" "-XX:+UseParallelGC"
check_gc "G1" "-XX:+UseG1GC"
check_gc "Shenandoah" "-XX:+UseShenandoahGC"
check_gc "ZGC" "-XX:+UseZGC"

echo
echo "=== Study repository ==="
STUDY_ROOT="${GC_SERVER_STUDY_ROOT:-$HOME/gc-explicit-cadence-study}"
test -d "$STUDY_ROOT"
echo "$STUDY_ROOT"

echo
echo "=== Core files ==="
test -f "$STUDY_ROOT/scripts/dacapo/run_subject.sh"
test -f "$STUDY_ROOT/parsers/parse_jfr_gc.py"
test -f "$STUDY_ROOT/instrumentation/CadenceCallback.java"

echo
echo "Server pre-flight completed."
