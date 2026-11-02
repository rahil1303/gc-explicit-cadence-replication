#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDY_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

PETCLINIC_HOME="${STUDY_ROOT}/subjects/services/spring-petclinic-rest"
SABLECC_HOME="${STUDY_ROOT}/subjects/services/sablecc-3.7"
TRAIN_TICKET_HOME="${STUDY_ROOT}/subjects/services/train-ticket"
DACAPO_HOME="${STUDY_ROOT}/subjects/dacapo"

PETCLINIC_JAR="${PETCLINIC_HOME}/target/spring-petclinic-rest-4.0.2.jar"
TRAIN_TICKET_JAR="${TRAIN_TICKET_HOME}/ts-user-service/target/ts-user-service-1.0.jar"
CADENCE_SOURCE="${STUDY_ROOT}/instrumentation/CadenceCallback.java"
CADENCE_CLASS="${STUDY_ROOT}/instrumentation/CadenceCallback.class"

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

section() {
    echo
    echo "============================================================"
    echo "$1"
    echo "============================================================"
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

section "Study root"
echo "${STUDY_ROOT}"
test -f "${STUDY_ROOT}/README.md" || fail "This does not look like the study repository."

section "Core build tools"
need_cmd java
need_cmd javac
need_cmd python3
need_cmd ant
need_cmd mvn

JAVA_MAJOR="$(java -version 2>&1 | awk -F'[\".]' '/version/ {print ($2=="1" ? $3 : $2); exit}')"
echo "Default Java major version: ${JAVA_MAJOR:-unknown}"

if [ "${JAVA_MAJOR:-}" != "21" ]; then
    fail "Default java must be JDK 21 for the production runtime. Current major version: ${JAVA_MAJOR:-unknown}"
fi

echo "java:   $(command -v java)"
echo "javac:  $(command -v javac)"
echo "python: $(command -v python3)"
echo "ant:    $(command -v ant)"
echo "mvn:    $(command -v mvn)"

section "DaCapo dependency"

DACAPO_JAR=""
for candidate in \
    "${DACAPO_HOME}/dacapo-23.11-MR2-chopin.jar" \
    "${DACAPO_HOME}/dacapo-23.11-chopin.jar" \
    "${DACAPO_HOME}/dacapo.jar"
do
    if [ -f "$candidate" ]; then
        DACAPO_JAR="$candidate"
        break
    fi
done

if [ -z "$DACAPO_JAR" ]; then
    echo "DaCapo JAR was not found under:"
    echo "  ${DACAPO_HOME}"
    echo
    echo "The DaCapo runtime is intentionally not stored in Git."
    echo "Place the Chopin JAR in subjects/dacapo/ and rerun this script."
    fail "Cannot compile CadenceCallback without the DaCapo JAR."
fi

echo "DaCapo JAR: ${DACAPO_JAR}"

section "Compile DaCapo cadence callback"

test -f "$CADENCE_SOURCE" || fail "Missing ${CADENCE_SOURCE}"

javac \
    -cp "$DACAPO_JAR" \
    -d "${STUDY_ROOT}/instrumentation" \
    "$CADENCE_SOURCE"

test -f "$CADENCE_CLASS" || fail "CadenceCallback.class was not produced."
echo "Built: ${CADENCE_CLASS}"

section "Build SableCC"

test -f "${SABLECC_HOME}/build.xml" || fail "Missing SableCC build.xml"

(
    cd "$SABLECC_HOME"
    ant clean compile
)

test -f "${SABLECC_HOME}/classes/org/sablecc/sablecc/SableCC.class" \
    || fail "SableCC build did not produce the expected class files."

echo "SableCC build OK."

section "Build Spring PetClinic REST"

test -f "${PETCLINIC_HOME}/pom.xml" || fail "Missing PetClinic pom.xml"

(
    cd "$PETCLINIC_HOME"

    if [ -f "./mvnw" ]; then
        chmod +x ./mvnw
        ./mvnw -DskipTests clean package
    else
        mvn -DskipTests clean package
    fi
)

test -f "$PETCLINIC_JAR" \
    || fail "PetClinic build completed but expected JAR was not found: ${PETCLINIC_JAR}"

echo "PetClinic build OK: ${PETCLINIC_JAR}"

section "Build Train Ticket user service"

test -f "${TRAIN_TICKET_HOME}/pom.xml" || fail "Missing Train Ticket parent pom.xml"

ORIGINAL_JAVA_HOME="${JAVA_HOME:-}"
ORIGINAL_PATH="$PATH"

if [ -n "${JAVA11_HOME:-}" ]; then
    test -x "${JAVA11_HOME}/bin/java" || fail "JAVA11_HOME is set but invalid: ${JAVA11_HOME}"
    export JAVA_HOME="$JAVA11_HOME"
    export PATH="${JAVA_HOME}/bin:${ORIGINAL_PATH}"
    echo "Building Train Ticket with JAVA11_HOME=${JAVA11_HOME}"
else
    echo "JAVA11_HOME is not set."
    echo "Trying the Train Ticket build with the current JDK."
fi

set +e
(
    cd "$TRAIN_TICKET_HOME"
    mvn \
        -pl ts-user-service \
        -am \
        -DskipTests \
        clean package
)
TRAIN_BUILD_STATUS=$?
set -e

if [ -n "$ORIGINAL_JAVA_HOME" ]; then
    export JAVA_HOME="$ORIGINAL_JAVA_HOME"
else
    unset JAVA_HOME || true
fi
export PATH="$ORIGINAL_PATH"

if [ "$TRAIN_BUILD_STATUS" -ne 0 ]; then
    echo
    echo "Train Ticket build failed."
    echo "If this is a JDK compatibility failure, install/provide JDK 11 and rerun:"
    echo "  export JAVA11_HOME=/path/to/jdk-11"
    echo "  ./scripts/setup_server.sh"
    exit "$TRAIN_BUILD_STATUS"
fi

test -f "$TRAIN_TICKET_JAR" \
    || fail "Train Ticket build completed but expected JAR was not found: ${TRAIN_TICKET_JAR}"

echo "Train Ticket build OK: ${TRAIN_TICKET_JAR}"

section "Restore / verify Java 21 runtime"

need_cmd java
FINAL_JAVA_MAJOR="$(java -version 2>&1 | awk -F'[\".]' '/version/ {print ($2=="1" ? $3 : $2); exit}')"

if [ "${FINAL_JAVA_MAJOR:-}" != "21" ]; then
    fail "Runtime Java is no longer JDK 21 after setup."
fi

java -version

section "Execution permissions"

find "${STUDY_ROOT}/scripts" -type f -name "*.sh" -exec chmod +x {} +

echo "Shell scripts marked executable."

section "Bootstrap complete"

echo "Prepared:"
echo "  [OK] CadenceCallback.class"
echo "  [OK] SableCC classes"
echo "  [OK] PetClinic JAR"
echo "  [OK] Train Ticket user-service JAR"
echo
echo "Next, on the measurement server, run:"
echo "  ./scripts/orchestration/check_server.sh"
