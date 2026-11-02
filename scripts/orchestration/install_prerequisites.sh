#!/usr/bin/env bash
set -euo pipefail

# Machine-prerequisite bootstrap for gc-explicit-cadence-study.
#
# Intended target: Debian/Ubuntu Linux measurement server.
# This script installs/fetches external prerequisites only.
# It does NOT build the study subjects; run setup_server.sh afterwards.
#
# Optional environment variables:
#   AUTO_YES=1
#       Skip interactive confirmation prompts.
#
#   EXPERIMENT_RUNNER_GIT=https://github.com/<owner>/<repo>.git
#       Repository containing the ExperimentRunner used by the study.
#       If unset, the script searches common local locations and reports
#       ExperimentRunner as unresolved rather than guessing a repository.
#
#   EXPERIMENT_RUNNER_DIR=/path/to/experiment-runner
#       Desired or existing ExperimentRunner directory.
#
#   SKIP_MYSQL=1
#       Do not install/check MySQL.
#
#   SKIP_DACAPO=1
#       Do not download/check DaCapo.
#
#   SKIP_ENERGIBRIDGE=1
#       Do not install/check EnergiBridge.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STUDY_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${SCRIPT_DIR}/server_env.sh"

DACAPO_VERSION="23.11-MR2-chopin"
DACAPO_ZIP="dacapo-${DACAPO_VERSION}.zip"
DACAPO_JAR="dacapo-${DACAPO_VERSION}.jar"
DACAPO_URL="https://download.dacapobench.org/chopin/${DACAPO_ZIP}"
DACAPO_DIR="${STUDY_ROOT}/subjects/dacapo"
DACAPO_DEST="${DACAPO_DIR}/${DACAPO_JAR}"

ENERGIBRIDGE_VERSION="v0.0.7"
ENERGIBRIDGE_REPO="https://github.com/tdurieux/EnergiBridge.git"
ENERGIBRIDGE_SRC="${HOME}/.local/src/EnergiBridge"
ENERGIBRIDGE_BIN="${HOME}/.local/bin/energibridge"

EXPERIMENT_RUNNER_DIR="${EXPERIMENT_RUNNER_DIR:-${HOME}/experiment-runner}"

say() {
    printf '%s\n' "$*"
}

section() {
    echo
    echo "============================================================"
    echo "$1"
    echo "============================================================"
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

have() {
    command -v "$1" >/dev/null 2>&1
}

confirm() {
    local prompt="$1"

    if [ "${AUTO_YES:-0}" = "1" ]; then
        return 0
    fi

    read -r -p "${prompt} [y/N] " answer
    case "$answer" in
        y|Y|yes|YES|Yes) return 0 ;;
        *) return 1 ;;
    esac
}

require_linux() {
    [ "$(uname -s)" = "Linux" ] || fail "This bootstrap is intended for the Linux measurement server."
}

require_repo() {
    [ -f "${STUDY_ROOT}/README.md" ] || \
        fail "Repository root could not be resolved from ${SCRIPT_DIR}."
}

detect_debian() {
    [ -r /etc/os-release ] || return 1
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}:${ID_LIKE:-}" in
        debian:*|ubuntu:*|*:debian*|*:ubuntu*) return 0 ;;
        *) return 1 ;;
    esac
}

sudo_cmd() {
    have sudo || fail "sudo is required for package installation."
    sudo "$@"
}

install_base_packages() {
    section "Base packages"

    if ! detect_debian; then
        fail "Automatic package installation currently supports Debian/Ubuntu only. Install git, curl, wget, unzip, gpg, Python 3, Maven, Ant, Rust/Cargo, build tools and MySQL manually, then rerun."
    fi

    say "Installing/checking OS packages..."
    sudo_cmd apt-get update
    sudo_cmd apt-get install -y \
        git \
        curl \
        wget \
        unzip \
        ca-certificates \
        apt-transport-https \
        gpg \
        jq \
        python3 \
        python3-pip \
        python3-venv \
        maven \
        ant \
        build-essential \
        pkg-config \
        libssl-dev \
        rustc \
        cargo

    if [ "${SKIP_MYSQL:-0}" != "1" ]; then
        sudo_cmd apt-get install -y mysql-server
    fi
}

configure_adoptium_repo() {
    section "Eclipse Temurin repository"

    if dpkg -s temurin-21-jdk >/dev/null 2>&1 && \
       dpkg -s temurin-11-jdk >/dev/null 2>&1; then
        say "Temurin 21 and Temurin 11 are already installed."
        return
    fi

    sudo_cmd apt-get install -y wget apt-transport-https gpg

    wget -qO - https://packages.adoptium.net/artifactory/api/gpg/key/public \
        | gpg --dearmor \
        | sudo tee /etc/apt/trusted.gpg.d/adoptium.gpg >/dev/null

    # shellcheck disable=SC1091
    . /etc/os-release
    local codename="${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}"
    [ -n "$codename" ] || fail "Could not determine Debian/Ubuntu codename for the Adoptium repository."

    echo "deb https://packages.adoptium.net/artifactory/deb ${codename} main" \
        | sudo tee /etc/apt/sources.list.d/adoptium.list >/dev/null

    sudo_cmd apt-get update
    sudo_cmd apt-get install -y temurin-21-jdk temurin-11-jdk
}

resolve_java_homes() {
    section "Resolve Java installations"

    JAVA21_HOME="$(dirname "$(dirname "$(readlink -f "$(dpkg -L temurin-21-jdk | grep '/bin/java$' | head -n 1)")")")"
    JAVA11_HOME="$(dirname "$(dirname "$(readlink -f "$(dpkg -L temurin-11-jdk | grep '/bin/java$' | head -n 1)")")")"

    [ -x "${JAVA21_HOME}/bin/java" ] || fail "Temurin 21 java executable not found."
    [ -x "${JAVA11_HOME}/bin/java" ] || fail "Temurin 11 java executable not found."

    say "JAVA21_HOME=${JAVA21_HOME}"
    say "JAVA11_HOME=${JAVA11_HOME}"

    "${JAVA21_HOME}/bin/java" -version
    "${JAVA11_HOME}/bin/java" -version
}

install_energibridge() {
    section "EnergiBridge"

    if [ "${SKIP_ENERGIBRIDGE:-0}" = "1" ]; then
        say "Skipping EnergiBridge by request."
        ENERGIBRIDGE_RESOLVED=""
        return
    fi

    if have energibridge; then
        ENERGIBRIDGE_RESOLVED="$(command -v energibridge)"
        say "Found existing EnergiBridge: ${ENERGIBRIDGE_RESOLVED}"
        return
    fi

    if [ -x "${ENERGIBRIDGE_BIN}" ]; then
        ENERGIBRIDGE_RESOLVED="${ENERGIBRIDGE_BIN}"
        say "Found existing EnergiBridge: ${ENERGIBRIDGE_RESOLVED}"
        return
    fi

    mkdir -p "$(dirname "${ENERGIBRIDGE_SRC}")" "$(dirname "${ENERGIBRIDGE_BIN}")"

    if [ -d "${ENERGIBRIDGE_SRC}/.git" ]; then
        say "Using existing EnergiBridge source checkout."
        git -C "${ENERGIBRIDGE_SRC}" fetch --tags
    else
        git clone "${ENERGIBRIDGE_REPO}" "${ENERGIBRIDGE_SRC}"
    fi

    git -C "${ENERGIBRIDGE_SRC}" checkout "${ENERGIBRIDGE_VERSION}"

    (
        cd "${ENERGIBRIDGE_SRC}"
        cargo build --release
    )

    cp "${ENERGIBRIDGE_SRC}/target/release/energibridge" "${ENERGIBRIDGE_BIN}"
    chmod +x "${ENERGIBRIDGE_BIN}"

    # EnergiBridge's Linux documentation requires raw I/O capability.
    if have setcap; then
        sudo setcap cap_sys_rawio=ep "${ENERGIBRIDGE_BIN}" || \
            say "WARNING: could not set cap_sys_rawio on EnergiBridge."
    else
        sudo_cmd apt-get install -y libcap2-bin
        sudo setcap cap_sys_rawio=ep "${ENERGIBRIDGE_BIN}" || \
            say "WARNING: could not set cap_sys_rawio on EnergiBridge."
    fi

    ENERGIBRIDGE_RESOLVED="${ENERGIBRIDGE_BIN}"
    say "Installed EnergiBridge: ${ENERGIBRIDGE_RESOLVED}"

    say
    say "NOTE: Linux energy-counter access may additionally require msr permissions."
    say "The later check_server.sh / smoke run should verify actual measurement access."
}

install_dacapo() {
    section "DaCapo ${DACAPO_VERSION}"

    if [ "${SKIP_DACAPO:-0}" = "1" ]; then
        say "Skipping DaCapo by request."
        DACAPO_RESOLVED=""
        return
    fi

    mkdir -p "${DACAPO_DIR}"

    if [ -f "${DACAPO_DEST}" ]; then
        DACAPO_RESOLVED="${DACAPO_DEST}"
        say "Found existing DaCapo JAR: ${DACAPO_RESOLVED}"
        return
    fi

    say "DaCapo ${DACAPO_VERSION} is not present."
    say "The official archive is large (several GB)."
    say "Source: ${DACAPO_URL}"

    if ! confirm "Download and extract the pinned DaCapo archive now?"; then
        say "DaCapo left unresolved."
        DACAPO_RESOLVED=""
        return
    fi

    local tmpdir
    tmpdir="$(mktemp -d)"
    trap 'rm -rf "${tmpdir:-}"' RETURN

    wget --continue -O "${tmpdir}/${DACAPO_ZIP}" "${DACAPO_URL}"
    unzip -q "${tmpdir}/${DACAPO_ZIP}" -d "${tmpdir}/unzipped"

    local found_jar
    found_jar="$(find "${tmpdir}/unzipped" -type f -name "${DACAPO_JAR}" -print -quit)"

    [ -n "${found_jar}" ] || fail "Downloaded DaCapo archive did not contain ${DACAPO_JAR}."

    cp "${found_jar}" "${DACAPO_DEST}"
    DACAPO_RESOLVED="${DACAPO_DEST}"

    say "Installed DaCapo JAR: ${DACAPO_RESOLVED}"
}

resolve_experiment_runner() {
    section "ExperimentRunner"

    local candidates=(
        "${EXPERIMENT_RUNNER_DIR}"
        "${HOME}/dacapo-gc-study/experiment-runner"
        "${HOME}/experiment-runner"
    )

    local candidate
    for candidate in "${candidates[@]}"; do
        if [ -d "${candidate}" ]; then
            EXPERIMENT_RUNNER_RESOLVED="$(cd "${candidate}" && pwd)"
            say "Found ExperimentRunner: ${EXPERIMENT_RUNNER_RESOLVED}"
            return
        fi
    done

    if [ -n "${EXPERIMENT_RUNNER_GIT:-}" ]; then
        mkdir -p "$(dirname "${EXPERIMENT_RUNNER_DIR}")"
        git clone "${EXPERIMENT_RUNNER_GIT}" "${EXPERIMENT_RUNNER_DIR}"
        EXPERIMENT_RUNNER_RESOLVED="$(cd "${EXPERIMENT_RUNNER_DIR}" && pwd)"
        say "Cloned ExperimentRunner: ${EXPERIMENT_RUNNER_RESOLVED}"
        return
    fi

    EXPERIMENT_RUNNER_RESOLVED=""
    say "ExperimentRunner was not found."
    say "No repository was guessed because this is an external study dependency."
    say "Later, either place it at:"
    say "  ${EXPERIMENT_RUNNER_DIR}"
    say "or rerun with:"
    say "  EXPERIMENT_RUNNER_GIT=<git-url> ./scripts/orchestration/install_prerequisites.sh"
}

check_mysql() {
    section "MySQL"

    if [ "${SKIP_MYSQL:-0}" = "1" ]; then
        say "Skipping MySQL by request."
        return
    fi

    if have mysql; then
        say "MySQL client: $(command -v mysql)"
    else
        say "WARNING: mysql client was not found after package installation."
    fi

    if have systemctl && systemctl list-unit-files mysql.service >/dev/null 2>&1; then
        sudo systemctl enable --now mysql || \
            say "WARNING: MySQL service could not be started automatically."
    fi

    say "MySQL installation is handled here; Train Ticket schema/data initialisation remains part of study setup/validation."
}

write_env_file() {
    section "Write resolved environment"

    cat > "${ENV_FILE}" <<EOF
#!/usr/bin/env bash
# Generated by install_prerequisites.sh.
# Re-run the installer if paths change.

export GC_SERVER_STUDY_ROOT="${STUDY_ROOT}"
export JAVA21_HOME="${JAVA21_HOME}"
export JAVA11_HOME="${JAVA11_HOME}"
export ENERGIBRIDGE_BIN="${ENERGIBRIDGE_RESOLVED}"
export EXPERIMENT_RUNNER="${EXPERIMENT_RUNNER_RESOLVED}"
export DACAPO_JAR="${DACAPO_RESOLVED}"

# Java 21 is the production experiment runtime.
export JAVA_HOME="\${JAVA21_HOME}"
export PATH="\${JAVA21_HOME}/bin:${HOME}/.local/bin:\${PATH}"
EOF

    chmod +x "${ENV_FILE}"
    say "Wrote: ${ENV_FILE}"
}

summary() {
    section "Prerequisite bootstrap summary"

    printf '%-24s %s\n' "Study root:" "${STUDY_ROOT}"
    printf '%-24s %s\n' "Temurin 21:" "${JAVA21_HOME}"
    printf '%-24s %s\n' "Temurin 11:" "${JAVA11_HOME}"
    printf '%-24s %s\n' "Maven:" "$(command -v mvn)"
    printf '%-24s %s\n' "Ant:" "$(command -v ant)"
    printf '%-24s %s\n' "Python 3:" "$(command -v python3)"
    printf '%-24s %s\n' "Git:" "$(command -v git)"
    printf '%-24s %s\n' "EnergiBridge:" "${ENERGIBRIDGE_RESOLVED:-UNRESOLVED}"
    printf '%-24s %s\n' "DaCapo:" "${DACAPO_RESOLVED:-UNRESOLVED}"
    printf '%-24s %s\n' "ExperimentRunner:" "${EXPERIMENT_RUNNER_RESOLVED:-UNRESOLVED}"
    printf '%-24s %s\n' "Environment file:" "${ENV_FILE}"

    echo
    if [ -z "${DACAPO_RESOLVED:-}" ] || \
       [ -z "${ENERGIBRIDGE_RESOLVED:-}" ] || \
       [ -z "${EXPERIMENT_RUNNER_RESOLVED:-}" ]; then
        say "Prerequisites are PARTIALLY READY."
        say "Resolve the UNRESOLVED item(s), rerun this script, then continue."
    else
        say "External prerequisites are READY."
        say
        say "Next:"
        say "  source scripts/orchestration/server_env.sh"
        say "  ./scripts/orchestration/setup_server.sh"
        say "  ./scripts/orchestration/check_server.sh"
    fi
}

main() {
    require_linux
    require_repo

    section "Prerequisite bootstrap"
    say "Repository: ${STUDY_ROOT}"
    say "This script may use sudo and download several external dependencies."

    if ! confirm "Continue?"; then
        say "Cancelled."
        exit 0
    fi

    install_base_packages
    configure_adoptium_repo
    resolve_java_homes
    install_energibridge
    install_dacapo
    resolve_experiment_runner
    check_mysql
    write_env_file
    summary
}

main "$@"
