#!/bin/bash
#
# Codex Subscription Router - macOS installer.
#
# Downloads or updates the source, installs the locked build tools, builds the
# independent copy of the official ChatGPT app, and opens it. The official app is
# only read; it is never modified.
#
# Missing tools are offered with Homebrew, a newer ChatGPT build than the project
# has recorded is explained and asked about, and a Mac without an Apple signing
# certificate is offered a basic (ad-hoc) signature. Questions are asked on the
# terminal, so `curl ... | /bin/bash` works. Where nobody can answer (another
# program is running this), set the matching variable instead:
#
#   CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES=1            answer yes to every question
#   CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE=1 build from an unrecorded ChatGPT build
#   CODEX_SUBSCRIPTION_ROUTER_ALLOW_ADHOC_SIGNING=1   sign without an Apple certificate
#   CODEX_SUBSCRIPTION_ROUTER_NO_LAUNCH=1             build without opening the app
#   CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR=<path>       where the one-liner keeps its checkout
#   CODEX_SUBSCRIPTION_ROUTER_DRY_RUN=1               only define the functions (for tests)
#   CODEX_SUBSCRIPTION_ROUTER_TTY=<file>              where answers are read from (default /dev/tty; for tests)
#
# macOS ships bash 3.2, so this file avoids anything newer (associative arrays,
# mapfile, ${var,,}) and never expands an empty array under `set -u`.

set -euo pipefail

readonly REPOSITORY_URL="https://github.com/Mvnshi/codex-subscription-router.git"
readonly SOURCE_BRANCH="main"
readonly DEFAULT_SOURCE_DIR="${HOME}/.codex-subscription-router-mvnshi/source"
readonly SOURCE_DIR="${CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR:-${DEFAULT_SOURCE_DIR}}"
readonly DESTINATION_APP="${HOME}/Applications/Codex Subscription Router.app"
readonly DESTINATION_HELPER="${HOME}/Applications/Codex Subscription Router Computer Use.app"

log() {
    printf '\n==> %s\n' "$1" >&2
}

fail() {
    printf '\nInstall failed: %s\n' "$1" >&2
    exit 1
}

# Asks a yes/no question of a person. Returns 0 for yes (also the default), 1 for
# no, and 2 when nobody can be asked. The answer is read from the terminal because
# under `curl | bash` stdin is the script itself.
ask_yes_no() {
    local question="$1"
    local answer=""
    local tty_path="${CODEX_SUBSCRIPTION_ROUTER_TTY:-/dev/tty}"
    if [ "${CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES:-}" = "1" ]; then
        return 0
    fi
    if ! ( : < "${tty_path}" ) 2>/dev/null; then
        return 2
    fi
    printf '%s [Y/n] ' "${question}" >&2
    IFS= read -r answer < "${tty_path}" || answer=""
    case "${answer}" in
        n|N|no|No|NO) return 1 ;;
    esac
    return 0
}

# Homebrew's location on Apple silicon is not on the PATH of every shell.
load_homebrew() {
    if ! command -v brew >/dev/null 2>&1 && [ -x /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
}

# Tries to provide what is missing: the Xcode Command Line Tools (git, python3,
# clang, codesign) through Apple's own installer, Go and Node.js through Homebrew.
# Anything it cannot or may not do is left for the caller's error message.
offer_missing_tools() {
    local missing="$1"
    local formulae=""
    local answer=0

    case " ${missing} " in
        *" git "*|*" python3 "*|*" security "*|*" xcrun "*)
            ask_yes_no "The Xcode Command Line Tools (Apple's free developer tools) are missing. Start their installer now?" || answer=$?
            if [ "${answer}" -eq 0 ]; then
                xcode-select --install >/dev/null 2>&1 || true
                fail "finish the Xcode Command Line Tools installer that just opened (it can take several minutes), then run this command again."
            fi
            ;;
    esac

    case " ${missing} " in *" go "*) formulae="${formulae} go" ;; esac
    case " ${missing} " in *" node "*|*" npm "*) formulae="${formulae} node" ;; esac
    if [ -z "${formulae}" ]; then
        return
    fi
    load_homebrew
    if ! command -v brew >/dev/null 2>&1; then
        return
    fi
    answer=0
    ask_yes_no "Install${formulae} with Homebrew now?" || answer=$?
    if [ "${answer}" -eq 0 ]; then
        # shellcheck disable=SC2086 # the formula list is meant to split
        brew install ${formulae} >&2
    fi
}

# A signing identity Apple's tools will use: one named in the environment, or a
# team-backed certificate in the keychain.
has_signing_identity() {
    if [ -n "${CODEX_MUX_SIGNING_IDENTITY:-}" ]; then
        return 0
    fi
    local identities
    identities="$(security find-identity -v -p codesigning 2>/dev/null || true)"
    case "${identities}" in
        *"Developer ID Application"*|*"Apple Development"*) return 0 ;;
    esac
    return 1
}

node_is_supported() {
    local version
    version="$("$1" -p 'process.versions.node' 2>/dev/null)" || return 1
    local major="${version%%.*}"
    local rest="${version#*.}"
    local minor="${rest%%.*}"
    [ "${major}" -gt 22 ] || { [ "${major}" -eq 22 ] && [ "${minor}" -ge 12 ]; }
}

# A shell's default Node (often an old nvm alias) may be too old even though a
# supported version is installed. Prefer the newest supported nvm install over
# failing, without changing the user's nvm default.
use_supported_nvm_node() {
    local nvm_root="${NVM_DIR:-${HOME}/.nvm}/versions/node"
    [ -d "${nvm_root}" ] || return 1
    local candidate
    local best=""
    for candidate in $(ls -1 "${nvm_root}" | sort -t. -k1,1V -k2,2n -k3,3n); do
        if [ -x "${nvm_root}/${candidate}/bin/node" ] && node_is_supported "${nvm_root}/${candidate}/bin/node"; then
            best="${nvm_root}/${candidate}/bin"
        fi
    done
    [ -n "${best}" ] || return 1
    export PATH="${best}:${PATH}"
    log "Using Node.js $(node --version) from ${best}"
}

require_prerequisites() {
    if [ "$(uname -s)" != "Darwin" ]; then
        fail "Codex Subscription Router supports macOS only."
    fi
    if [ "$(uname -m)" != "arm64" ]; then
        fail "Codex Subscription Router currently requires Apple silicon."
    fi
    if [ ! -d "/Applications/ChatGPT.app" ]; then
        fail "install the official ChatGPT app in /Applications first."
    fi

    if ! command -v node >/dev/null 2>&1 || ! node_is_supported node; then
        use_supported_nvm_node || true
    fi

    local missing=()
    local command_name
    for command_name in git go node npm python3 security xcrun; do
        if ! command -v "${command_name}" >/dev/null 2>&1; then
            missing+=("${command_name}")
        fi
    done
    if [ "${#missing[@]}" -ne 0 ]; then
        offer_missing_tools "${missing[*]}"
        # Look again: Homebrew may have just provided Go and Node.js.
        local still_missing=()
        for command_name in "${missing[@]}"; do
            if ! command -v "${command_name}" >/dev/null 2>&1; then
                still_missing+=("${command_name}")
            fi
        done
        if [ "${#still_missing[@]}" -ne 0 ]; then
            fail "missing prerequisites: ${still_missing[*]}. Install Xcode Command Line Tools (xcode-select --install), Go 1.26+ and Node.js 22.12+ (with Homebrew: brew install go node), then rerun this command."
        fi
    fi

    local node_major
    local node_minor
    node_major="$(node -p 'process.versions.node.split(".")[0]')"
    node_minor="$(node -p 'process.versions.node.split(".")[1]')"
    if [ "${node_major}" -lt 22 ] || { [ "${node_major}" -eq 22 ] && [ "${node_minor}" -lt 12 ]; }; then
        fail "Node.js 22.12 or newer is required; found $(node --version)."
    fi

    local go_version
    local go_major
    local go_minor
    go_version="$(go env GOVERSION | sed 's/^go//')"
    go_major="${go_version%%.*}"
    go_minor="${go_version#*.}"
    go_minor="${go_minor%%.*}"
    if [ "${go_major}" -lt 1 ] || { [ "${go_major}" -eq 1 ] && [ "${go_minor}" -lt 26 ]; }; then
        fail "Go 1.26 or newer is required; found go${go_version}."
    fi
}

resolve_source_dir() {
    local script_source="${BASH_SOURCE[0]:-}"
    local script_dir=""
    if [ -n "${script_source}" ] && [ -f "${script_source}" ]; then
        script_dir="$(CDPATH= cd -- "$(dirname -- "${script_source}")" && pwd)"
    fi
    if [ -n "${script_dir}" ] && [ -f "${script_dir}/scripts/patch_app.py" ]; then
        printf '%s\n' "${script_dir}"
        return
    fi

    if [ -d "${SOURCE_DIR}/.git" ]; then
        if [ -n "$(git -C "${SOURCE_DIR}" status --porcelain)" ]; then
            fail "${SOURCE_DIR} has local changes; preserve or commit them before updating."
        fi
        if [ "$(git -C "${SOURCE_DIR}" branch --show-current)" != "${SOURCE_BRANCH}" ]; then
            fail "${SOURCE_DIR} is not on ${SOURCE_BRANCH}; switch branches or set CODEX_SUBSCRIPTION_ROUTER_SOURCE_DIR."
        fi
        log "Updating source"
        git -C "${SOURCE_DIR}" pull --ff-only origin "${SOURCE_BRANCH}" >&2
    elif [ -e "${SOURCE_DIR}" ]; then
        fail "${SOURCE_DIR} exists but is not a Git repository."
    else
        log "Downloading source"
        mkdir -p "$(dirname -- "${SOURCE_DIR}")"
        git clone --depth 1 --branch "${SOURCE_BRANCH}" "${REPOSITORY_URL}" "${SOURCE_DIR}" >&2
    fi
    printf '%s\n' "${SOURCE_DIR}"
}

stop_bundle_processes() {
    local bundle_path="$1"
    local process_id
    local command_line
    local attempt

    for attempt in 1 2 3 4 5 6 7 8 9 10; do
        local found_process="false"
        for process_id in $(pgrep -f "${bundle_path}/Contents/" 2>/dev/null || true); do
            command_line="$(ps -p "${process_id}" -o command= 2>/dev/null || true)"
            case "${command_line}" in
                "${bundle_path}/Contents/"*)
                    found_process="true"
                    kill "${process_id}" 2>/dev/null || true
                    ;;
            esac
        done
        if [ "${found_process}" = "false" ]; then
            return
        fi
        sleep 1
    done
    fail "could not stop processes belonging to ${bundle_path}."
}

# The ChatGPT app on this Mac is a build the project has not recorded. Say what that
# means in plain words and ask. The patcher still refuses by itself if anything it
# expects has changed, and the official app is never modified.
confirm_untested_source() {
    local answer=0
    if [ "${CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE:-}" = "1" ]; then
        return
    fi
    {
        printf '\nThe ChatGPT app on this Mac is newer than the versions this project has tested.\n'
        printf 'Codex Subscription Router can still be built from it: every step checks that the app\n'
        printf 'looks exactly as expected and stops by itself if it does not, and your ChatGPT app is\n'
        printf 'never changed. The only risk is that something in the router misbehaves on this version.\n'
    } >&2
    ask_yes_no "Build it anyway?" || answer=$?
    case "${answer}" in
        0) ;;
        1) fail "stopped at your request. Nothing was changed." ;;
        *) fail "the ChatGPT app is a build the project has not tested, and nobody can be asked here. Set CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE=1 to continue anyway." ;;
    esac
}

# No Apple signing certificate was found. A basic (ad-hoc) signature is enough for
# the app to run, accounts, routing and usage included; Computer Use and Appshots
# need a certificate from an Apple developer account to get macOS privacy grants.
confirm_adhoc_signing() {
    local answer=0
    {
        printf '\nNo Apple signing certificate was found on this Mac.\n'
        printf 'Codex Subscription Router can be built with a basic signature instead. Accounts, routing\n'
        printf 'and usage work; Computer Use and Appshots may not (they need a certificate from an Apple\n'
        printf 'developer account, which is free to create in Xcode).\n'
    } >&2
    ask_yes_no "Build with a basic signature?" || answer=$?
    case "${answer}" in
        0) ;;
        1) fail "stopped at your request. Nothing was changed." ;;
        *) fail "no signing certificate was found, and nobody can be asked here. Set CODEX_SUBSCRIPTION_ROUTER_ALLOW_ADHOC_SIGNING=1 to build with a basic signature, or set CODEX_MUX_SIGNING_IDENTITY." ;;
    esac
}

main() {
    log "Checking this Mac"
    require_prerequisites

    local project_dir
    project_dir="$(resolve_source_dir)"
    cd "${project_dir}"

    log "Installing locked build tools"
    npm ci --ignore-scripts --no-audit --no-fund

    local patch_arguments=()
    if [ "${CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE:-}" = "1" ]; then
        patch_arguments+=("--allow-untested-source")
    fi
    if [ "${CODEX_SUBSCRIPTION_ROUTER_ALLOW_ADHOC_SIGNING:-}" = "1" ]; then
        patch_arguments+=("--allow-adhoc-signing")
    fi

    # Find out about the app before anything is stopped or built: a ChatGPT build the
    # project has not recorded is a question for the person, and answering "no" must
    # leave their running copy alone.
    log "Checking the ChatGPT app on this Mac"
    local check_status=0
    if [ "${#patch_arguments[@]}" -ne 0 ]; then
        python3 scripts/patch_app.py --check-source "${patch_arguments[@]}" || check_status=$?
    else
        python3 scripts/patch_app.py --check-source || check_status=$?
    fi
    if [ "${check_status}" -eq 3 ]; then
        confirm_untested_source
        patch_arguments+=("--allow-untested-source")
    elif [ "${check_status}" -ne 0 ]; then
        fail "the source check failed with exit code ${check_status}; the message above says why."
    fi

    # Without an Apple certificate the app can still be built with a basic signature.
    local arguments_text=" "
    if [ "${#patch_arguments[@]}" -ne 0 ]; then
        arguments_text=" ${patch_arguments[*]} "
    fi
    case "${arguments_text}" in
        *" --allow-adhoc-signing "*) ;;
        *)
            if ! has_signing_identity; then
                confirm_adhoc_signing
                patch_arguments+=("--allow-adhoc-signing")
            fi
            ;;
    esac

    if [ -d "${DESTINATION_APP}" ] || [ -d "${DESTINATION_HELPER}" ]; then
        log "Stopping the existing installation"
        stop_bundle_processes "${DESTINATION_APP}"
        stop_bundle_processes "${DESTINATION_HELPER}"
        patch_arguments+=("--force")
    fi

    log "Building and signing Codex Subscription Router (this takes a few minutes)"
    # macOS /bin/bash is 3.2; with set -u, expanding an empty array (e.g. "${patch_arguments[@]}") errors as “unbound variable”.
    if [ "${#patch_arguments[@]}" -ne 0 ]; then
        python3 scripts/patch_app.py "${patch_arguments[@]}"
    else
        python3 scripts/patch_app.py
    fi

    if [ "${CODEX_SUBSCRIPTION_ROUTER_NO_LAUNCH:-}" = "1" ]; then
        log "Skipping launch (CODEX_SUBSCRIPTION_ROUTER_NO_LAUNCH=1)"
    else
        log "Opening Codex Subscription Router"
        open "${DESTINATION_APP}"
    fi
    printf '\nCodex Subscription Router is installed.\n' >&2
    printf '  Open it any time from ~/Applications, or press Command+Space and type "Codex Subscription Router".\n' >&2
    printf '  Your normal ChatGPT app is untouched and keeps working as before.\n' >&2
    printf '\nInstalled successfully: %s\n' "${DESTINATION_APP}"
}

# CODEX_SUBSCRIPTION_ROUTER_DRY_RUN=1 only defines the functions, so the repository
# checks (scripts/test-install.sh) can exercise them without installing anything.
if [ "${CODEX_SUBSCRIPTION_ROUTER_DRY_RUN:-}" != "1" ]; then
    main "$@"
fi
