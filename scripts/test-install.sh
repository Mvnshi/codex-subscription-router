#!/bin/bash
# Tests for the functions in install.sh that decide things: the yes/no questions, the
# offers of missing tools, and the signing-identity check. Nothing is installed: each
# test sources install.sh in dry-run mode in its own subshell and replaces the commands
# that would touch the machine.
#
#   bash scripts/test-install.sh
#
# Written for macOS's bash 3.2 (CI runs it there) and also runs under newer bash.

set -u

root="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
failures=0
count=0
scratch="$(mktemp -d)"
trap 'rm -rf "${scratch}"' EXIT

pass() { count=$((count + 1)); printf 'ok    %s\n' "$1"; }
fail_test() { count=$((count + 1)); failures=$((failures + 1)); printf 'FAIL  %s\n      %s\n' "$1" "$2"; }

# check <name> <expected> <actual>
check() {
    if [ "$2" = "$3" ]; then pass "$1"; else fail_test "$1" "expected '$2' but got '$3'"; fi
}

# Each case runs in a fresh subshell that has install.sh's functions and nothing else of it.
load='export CODEX_SUBSCRIPTION_ROUTER_DRY_RUN=1; . "'"${root}"'/install.sh"; set +e'

printf 'y\n' > "${scratch}/yes"
printf 'n\n' > "${scratch}/no"
printf '\n' > "${scratch}/enter"

# --- ask_yes_no --------------------------------------------------------------------------
status="$(bash -c "${load}; CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES=1 CODEX_SUBSCRIPTION_ROUTER_TTY=/nonexistent ask_yes_no 'q' 2>/dev/null; echo \$?")"
check "ASSUME_YES answers yes without asking" 0 "${status}"

status="$(bash -c "${load}; CODEX_SUBSCRIPTION_ROUTER_TTY=/nonexistent ask_yes_no 'q' 2>/dev/null; echo \$?")"
check "nobody to ask returns 2" 2 "${status}"

status="$(bash -c "${load}; CODEX_SUBSCRIPTION_ROUTER_TTY='${scratch}/yes' ask_yes_no 'q' 2>/dev/null; echo \$?")"
check "an answer of y is yes" 0 "${status}"

status="$(bash -c "${load}; CODEX_SUBSCRIPTION_ROUTER_TTY='${scratch}/enter' ask_yes_no 'q' 2>/dev/null; echo \$?")"
check "pressing Enter is yes" 0 "${status}"

status="$(bash -c "${load}; CODEX_SUBSCRIPTION_ROUTER_TTY='${scratch}/no' ask_yes_no 'q' 2>/dev/null; echo \$?")"
check "an answer of n is no" 1 "${status}"

# The question is shown on stderr, so it also appears under `curl | bash` with stdout piped.
shown="$(bash -c "${load}; CODEX_SUBSCRIPTION_ROUTER_TTY='${scratch}/yes' ask_yes_no 'Install go?' 2>&1 >/dev/null")"
check "the question is printed on stderr" "Install go? [Y/n] " "${shown}"

# --- has_signing_identity --------------------------------------------------------------------
status="$(bash -c "${load}; security() { printf '  1) ABC \"Apple Development: Someone (TEAM)\"\n     1 valid identities found\n'; }; unset CODEX_MUX_SIGNING_IDENTITY; has_signing_identity; echo \$?")"
check "an Apple Development certificate counts" 0 "${status}"

status="$(bash -c "${load}; security() { printf '  1) ABC \"Developer ID Application: Corp (TEAM)\"\n'; }; unset CODEX_MUX_SIGNING_IDENTITY; has_signing_identity; echo \$?")"
check "a Developer ID Application certificate counts" 0 "${status}"

status="$(bash -c "${load}; security() { printf '     0 valid identities found\n'; }; unset CODEX_MUX_SIGNING_IDENTITY; has_signing_identity; echo \$?")"
check "no certificate is reported as none" 1 "${status}"

status="$(bash -c "${load}; security() { return 1; }; unset CODEX_MUX_SIGNING_IDENTITY; has_signing_identity; echo \$?")"
check "a failing keychain query is reported as none" 1 "${status}"

status="$(bash -c "${load}; security() { printf '     0 valid identities found\n'; }; CODEX_MUX_SIGNING_IDENTITY='Developer ID Application: X (T)' has_signing_identity; echo \$?")"
check "an identity named in the environment counts" 0 "${status}"

# The check must not depend on a pipe that pipefail could turn into a failure.
status="$(bash -c "set -o pipefail; ${load}; security() { for i in 1 2 3 4 5 6 7 8 9 10; do printf '  line %s\n' \$i; done; printf '\"Apple Development: A (T)\"\n'; }; has_signing_identity; echo \$?")"
check "a match is found under pipefail" 0 "${status}"

# --- offer_missing_tools ---------------------------------------------------------------------------
# Go and Node.js missing, Homebrew present, yes: both formulae are installed together.
out="$(bash -c "${load}; brew() { echo \"brew \$*\"; }; command() { if [ \"\$1\" = -v ] && [ \"\$2\" = brew ]; then return 0; fi; builtin command \"\$@\"; }; CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES=1 offer_missing_tools 'go node npm' 2>&1")"
check "go and node are installed with one brew command" "brew install go node" "${out}"

# Only Go missing.
out="$(bash -c "${load}; brew() { echo \"brew \$*\"; }; command() { if [ \"\$1\" = -v ] && [ \"\$2\" = brew ]; then return 0; fi; builtin command \"\$@\"; }; CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES=1 offer_missing_tools 'go' 2>&1")"
check "only what is missing is installed" "brew install go" "${out}"

# Answering no installs nothing.
out="$(bash -c "${load}; brew() { echo \"brew \$*\"; }; command() { if [ \"\$1\" = -v ] && [ \"\$2\" = brew ]; then return 0; fi; builtin command \"\$@\"; }; CODEX_SUBSCRIPTION_ROUTER_TTY='${scratch}/no' offer_missing_tools 'go' 2>/dev/null; echo done")"
check "no installs nothing" "done" "${out}"

# Nobody to ask installs nothing either.
out="$(bash -c "${load}; brew() { echo \"brew \$*\"; }; command() { if [ \"\$1\" = -v ] && [ \"\$2\" = brew ]; then return 0; fi; builtin command \"\$@\"; }; CODEX_SUBSCRIPTION_ROUTER_TTY=/nonexistent offer_missing_tools 'go' 2>/dev/null; echo done")"
check "no one to ask installs nothing" "done" "${out}"

# The Xcode tools are offered through Apple's installer (which opens its own window), and the run
# stops so the person can finish it.
rm -f "${scratch}/xcode-called"
out="$(bash -c "${load}; xcode-select() { echo \"\$*\" > '${scratch}/xcode-called'; }; CODEX_SUBSCRIPTION_ROUTER_ASSUME_YES=1 offer_missing_tools 'git python3' 2>&1")"
case "${out}" in
    *"finish the Xcode Command Line Tools installer"*) pass "missing developer tools stop the run until Apple's installer is finished" ;;
    *) fail_test "missing developer tools stop the run until Apple's installer is finished" "${out}" ;;
esac
check "Apple's installer is started with xcode-select --install" "--install" "$(cat "${scratch}/xcode-called" 2>/dev/null)"

# --- the untested-build and ad-hoc questions ------------------------------------------------------------
status="$(bash -c "${load}; CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE=1 confirm_untested_source 2>/dev/null; echo \$?")"
check "an allowed untested build asks nothing" 0 "${status}"

out="$(bash -c "${load}; CODEX_SUBSCRIPTION_ROUTER_TTY=/nonexistent confirm_untested_source 2>&1; echo \$?" )"
case "${out}" in
    *"CODEX_SUBSCRIPTION_ROUTER_ALLOW_UNTESTED_SOURCE=1"*) pass "an untested build with no one to ask names the setting" ;;
    *) fail_test "an untested build with no one to ask names the setting" "${out}" ;;
esac

out="$(bash -c "${load}; CODEX_SUBSCRIPTION_ROUTER_TTY='${scratch}/no' confirm_untested_source 2>&1; echo \$?" )"
case "${out}" in
    *"Nothing was changed"*) pass "answering no to an untested build stops without changes" ;;
    *) fail_test "answering no to an untested build stops without changes" "${out}" ;;
esac

status="$(bash -c "${load}; CODEX_SUBSCRIPTION_ROUTER_TTY='${scratch}/yes' confirm_untested_source 2>/dev/null; echo \$?")"
check "answering yes to an untested build continues" 0 "${status}"

out="$(bash -c "${load}; CODEX_SUBSCRIPTION_ROUTER_TTY=/nonexistent confirm_adhoc_signing 2>&1; echo \$?" )"
case "${out}" in
    *"CODEX_SUBSCRIPTION_ROUTER_ALLOW_ADHOC_SIGNING=1"*) pass "no certificate with no one to ask names the setting" ;;
    *) fail_test "no certificate with no one to ask names the setting" "${out}" ;;
esac

status="$(bash -c "${load}; CODEX_SUBSCRIPTION_ROUTER_TTY='${scratch}/yes' confirm_adhoc_signing 2>/dev/null; echo \$?")"
check "answering yes to a basic signature continues" 0 "${status}"

# --- the patcher exit code the installer waits for ---------------------------------------------------------
if grep -q 'UNTESTED_SOURCE_EXIT_CODE = 3' "${root}/scripts/patch_app.py" && grep -q 'eq 3\|-eq 3' "${root}/install.sh"; then
    pass "install.sh and patch_app.py agree on exit code 3"
else
    fail_test "install.sh and patch_app.py agree on exit code 3" "the constant or the check is missing"
fi

printf '\n%s of %s install.sh tests passed\n' "$((count - failures))" "${count}"
[ "${failures}" -eq 0 ]
