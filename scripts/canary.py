#!/usr/bin/env python3
"""Turn what a canary job saw into one small result, and summarise the results of all jobs.

The canary (.github/workflows/upstream-canary.yml) installs the newest official build on a
clean machine, runs the real installer against it, and (on Windows) boots the patched app.
This reads the installer's log and the boot check's outcome and writes a result file the
report job, the tracking issues and scripts/record_build.py all understand.

    python scripts/canary.py result --platform windows-x64 --log install.log --exit-code 0 \\
        --boot ok --package 26.930.7945.0 --app-version 26.930.61225 --build 13232 --out result.json
    python scripts/canary.py summary results/*.json        # markdown for the job summary

Standard library only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PLATFORMS = ("macos", "windows-x64", "windows-arm64")
HASH = r"[0-9a-f]{64}"
# "Source ChatGPT version: 26.930.61225 (13232), app.asar <hash>" (macOS) and
# "Source Codex version: 154.0.8037.98 (file 154.0.8037.98), x64, signed, app.asar <hash>" (Windows).
IDENTITY = re.compile(r"Source (?P<name>.+?) version: (?P<version>\S+) \((?:file )?(?P<second>[^)]+)\).*?app\.asar (?P<asar>" + HASH + ")")
FAILURE = re.compile(r"^(?:patch failed|Install failed): (?P<message>.+)$")
# The installers end with a wrapper such as "the Windows patcher failed with exit code 1." that only points
# back at the real message printed earlier; it must not hide it.
GENERIC_FAILURE = re.compile(r"^the .{1,40} failed(?: with exit code \d+)?[.;]?(?: .*)?$")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def clean(text: str) -> str:
    return ANSI.sub("", text.replace("\r", ""))


def extract_identity(log: str) -> dict:
    """The official build the patcher saw, from its "Source ... version" line."""
    match = None
    for match in IDENTITY.finditer(clean(log)):
        pass  # the last one wins: a rerun after a prompt prints it again
    if match is None:
        return {}
    return {"host_version": match["version"], "host_second": match["second"], "asar": match["asar"]}


def extract_failure(log: str) -> str:
    """The last specific "patch failed" / "Install failed" message in the log, or "".

    A trailing wrapper ("the Windows patcher failed with exit code 1.") is only reported when nothing more
    specific was printed.
    """
    specific = generic = ""
    for line in clean(log).splitlines():
        found = FAILURE.match(line.strip())
        if found:
            message = found["message"].strip()
            if GENERIC_FAILURE.match(message):
                generic = message
            else:
                specific = message
    return specific or generic


def build_result(
    *,
    platform: str,
    log: str,
    exit_code: int,
    boot: str = "skipped",
    boot_detail: str = "",
    boot_informational: bool = False,
    package: str = "",
    app_version: str = "",
    build: str = "",
    run_url: str = "",
) -> dict:
    """status: pass | fail (the patch or install failed) | boot-failed | error (nothing to test)."""
    identity = extract_identity(log)
    failure = extract_failure(log)
    if exit_code != 0:
        status = "fail" if identity else "error"
    elif boot == "failed" and not boot_informational:
        status = "boot-failed"
    else:
        status = "pass"
    message = failure or (boot_detail if status == "boot-failed" else "")
    if status == "pass" and boot == "failed":
        # Reported, not hidden: the patch and the signature passed, the runner could not start the app.
        message = f"app did not start on the runner (not counted): {boot_detail}"
    if not message and status in ("fail", "error"):
        message = f"the installer exited with code {exit_code} and printed no explanation"
    result = {
        "platform": platform,
        "status": status,
        "message": message,
        "asar": identity.get("asar", ""),
        # macOS prints the app version and build; on Windows the host line names Chromium, so
        # the app's own version and build come from its package.json (passed in).
        "app_version": app_version or (identity.get("host_version", "") if platform == "macos" else ""),
        "build": build or (identity.get("host_second", "") if platform == "macos" else ""),
        "package": package,
        # Windows tables are keyed by the host executable's version pair (Chromium's).
        "host_version": identity.get("host_version", "") if platform != "macos" else "",
        "host_second": identity.get("host_second", "") if platform != "macos" else "",
        "boot": boot,
        "boot_detail": boot_detail,
        "run_url": run_url,
    }
    return result


ICONS = {"pass": "pass", "fail": "FAIL", "boot-failed": "FAIL (patched, did not boot)", "error": "could not run"}


def summary(results: list[dict]) -> str:
    lines = ["| Platform | Build | Result | Detail |", "| --- | --- | --- | --- |"]
    for result in sorted(results, key=lambda r: r["platform"]):
        build = result.get("package") or " ".join(
            part for part in (result.get("app_version"), f"({result['build']})" if result.get("build") else "") if part
        )
        detail = result.get("message") or (
            f"boot: {result['boot']}" if result.get("boot") not in ("", "skipped") else "anchors matched"
        )
        lines.append(f"| {result['platform']} | `{build or 'unknown'}` | {ICONS.get(result['status'], result['status'])} | {detail} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    result = commands.add_parser("result", help="write one result file from a log")
    result.add_argument("--platform", choices=PLATFORMS, required=True)
    result.add_argument("--log", type=Path, required=True)
    result.add_argument("--exit-code", type=int, required=True)
    result.add_argument("--boot", choices=("ok", "failed", "skipped"), default="skipped")
    result.add_argument("--boot-detail", default="")
    result.add_argument(
        "--boot-informational",
        action="store_true",
        help="a failed boot is reported but does not fail the result (macOS runners cannot answer GUI prompts)",
    )
    result.add_argument("--package", default="")
    result.add_argument("--app-version", default="")
    result.add_argument("--build", default="")
    result.add_argument("--run-url", default="")
    result.add_argument("--out", type=Path, required=True)

    table = commands.add_parser("summary", help="print a markdown table for result files")
    table.add_argument("files", nargs="+", type=Path)

    args = parser.parse_args(argv)
    if args.command == "result":
        log = args.log.read_text(encoding="utf-8", errors="replace") if args.log.is_file() else ""
        payload = build_result(
            platform=args.platform,
            log=log,
            exit_code=args.exit_code,
            boot=args.boot,
            boot_detail=args.boot_detail,
            boot_informational=args.boot_informational,
            package=args.package,
            app_version=args.app_version,
            build=args.build,
            run_url=args.run_url,
        )
        args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(payload))
        return 0
    results = [json.loads(path.read_text(encoding="utf-8")) for path in args.files]
    print(summary(results), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
