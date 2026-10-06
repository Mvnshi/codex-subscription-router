#!/bin/bash
# Codex Subscription Router - what to paste into an issue (macOS).
#
# Prints a short, read-only report: the macOS and tool versions, the ChatGPT app on this
# Mac (and whether the project has recorded that build), whether the router is installed
# and running, and what its accounts look like. Emails, tokens and chat contents are never
# printed. Nothing is changed.
#
#   curl -fsSL https://raw.githubusercontent.com/Mvnshi/codex-subscription-router/main/scripts/doctor.sh | /bin/bash
#
# or, from a clone:  bash scripts/doctor.sh

set -u

show() { printf '%-26s %s\n' "$1:" "$2"; }

tool_version() {
    # tool_version <command> <args...>: the first line of its version output, or "not found".
    local command_name="$1"
    shift
    if ! command -v "${command_name}" >/dev/null 2>&1; then
        printf 'not found'
        return
    fi
    "${command_name}" "$@" 2>&1 | head -n 1 || printf 'found, but did not run'
}

echo "Codex Subscription Router doctor"
echo "================================"
show "Date" "$(date '+%Y-%m-%d %H:%M %z')"
show "macOS" "$(sw_vers -productVersion 2>/dev/null || uname -sr) ($(uname -m))"

echo
echo "Tools"
show "  go" "$(tool_version go version)"
show "  node" "$(tool_version node --version)"
show "  python3" "$(tool_version python3 --version)"
show "  git" "$(tool_version git --version)"
show "  Xcode tools" "$(xcode-select -p 2>/dev/null || echo 'not installed (xcode-select --install)')"
identities="$(security find-identity -v -p codesigning 2>/dev/null | grep -Ec 'Developer ID Application|Apple Development' || true)"
show "  signing certificates" "${identities:-0} (0 means a basic signature will be offered)"

echo
echo "Official ChatGPT app"
app="/Applications/ChatGPT.app"
if [ -d "${app}" ]; then
    plist="${app}/Contents/Info.plist"
    version="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "${plist}" 2>/dev/null || echo unknown)"
    build="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "${plist}" 2>/dev/null || echo unknown)"
    show "  version" "${version} (build ${build})"
    show "  app.asar sha256" "$(shasum -a 256 "${app}/Contents/Resources/app.asar" 2>/dev/null | cut -d' ' -f1)"
else
    show "  installed" "no (expected at ${app})"
fi

echo
echo "Codex Subscription Router"
router="${HOME}/Applications/Codex Subscription Router.app"
if [ -d "${router}" ]; then
    show "  installed" "yes, built $(stat -f '%Sm' -t '%Y-%m-%d %H:%M' "${router}" 2>/dev/null || echo unknown)"
else
    show "  installed" "no"
fi
running="$(pgrep -fl 'Codex Subscription Router' 2>/dev/null | wc -l | tr -d ' ')"
show "  running processes" "${running}"

root="${HOME}/.codex-mux"
if [ -f "${root}/control-token" ] && [ "${running}" != "0" ] && command -v curl >/dev/null 2>&1; then
    token="$(tr -d '[:space:]' < "${root}/control-token")"
    health="$(curl -fsS -m 4 -H "X-Codex-Mux-Token: ${token}" http://127.0.0.1:48123/v1/health 2>/dev/null || true)"
    if [ -n "${health}" ]; then
        show "  control API" "healthy"
        curl -fsS -m 8 -H "X-Codex-Mux-Token: ${token}" http://127.0.0.1:48123/v1/accounts 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
routing = data.get("routing", {})
print("%-26s %s, resets: %s" % ("  routing:", routing.get("mode"), routing.get("resetPolicy")))
for index, account in enumerate(data.get("accounts", []), 1):
    limits = account.get("rateLimits") or {}
    windows = []
    for key in ("primary", "secondary"):
        window = limits.get(key)
        if window:
            windows.append("%s-minute window %s%% used" % (window.get("windowDurationMins"), window.get("usedPercent")))
    print("%-26s %s, connected=%s, enabled=%s, %s" % ("  account %d:" % index, account.get("planLabel"), account.get("connected"), account.get("enabled"), ", ".join(windows) or "usage unknown"))
' 2>/dev/null || true
    else
        show "  control API" "not reachable"
    fi
fi
if [ -f "${root}/engine-provider" ]; then
    show "  engine provider" "file: $(head -n 1 "${root}/engine-provider")"
fi
if [ -d "${root}/backups" ]; then
    show "  rebuild backups" "$(find "${root}/backups" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ') in ${root}/backups"
fi

echo
echo "Is this ChatGPT build recorded by the project?"
here="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/.." 2>/dev/null && pwd || true)"
for candidate in "${here}" "${HOME}/.codex-subscription-router-mvnshi/source"; do
    if [ -n "${candidate}" ] && [ -f "${candidate}/scripts/patch_app.py" ]; then
        if command -v python3 >/dev/null 2>&1 && [ -d "${app}" ]; then
            ( cd "${candidate}" && python3 scripts/patch_app.py --check-source 2>&1 | sed 's/^/  /' )
            echo "exit code: ${PIPESTATUS[0]:-?}  (0 = recorded or allowed, 3 = newer than the recorded builds, 1 = something else)"
        else
            echo "  skipped (needs python3 and the ChatGPT app)"
        fi
        exit 0
    fi
done
echo "  skipped (the project source was not found; run this from a clone)"
