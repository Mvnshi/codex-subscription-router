#!/usr/bin/env python3
"""Keep one tracking issue per platform for official builds the project has not recorded.

Official builds arrive about daily, so a new issue per build would bury the tracker. Instead
each platform has one rolling issue (found by a hidden marker in its body) that this updates:

- upstream-watch.yml calls `sync` with scripts/upstream_watch.py's JSON report. An unrecorded
  newest build opens or updates the platform's issue (a comment when a newer build appears);
  once the newest build is recorded the issue is closed.
- upstream-canary.yml calls `canary` with the result files of the day's canary. The result is
  written into the issue's canary section; a failing canary with no open issue opens one.

It talks to GitHub only through the `gh` CLI (GH_TOKEN in the environment), passed in as a
function so the tests can run without a network.

    python scripts/upstream_issues.py sync report.json
    python scripts/upstream_issues.py canary results/canary-*.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable

LABEL = "upstream-build"
KEYS = ("macos", "windows-x64", "windows-arm64")
NAMES = {"macos": "macOS", "windows-x64": "Windows x64", "windows-arm64": "Windows ARM64"}
CANARY_BEGIN = "<!-- canary:begin -->"
CANARY_END = "<!-- canary:end -->"
NO_CANARY = "_The daily canary has not reported on this build yet._"

Gh = Callable[[list[str]], str]


def marker(key: str) -> str:
    return f"<!-- upstream-watch:{key} -->"


def real_gh(arguments: list[str]) -> str:
    completed = subprocess.run(["gh", *arguments], capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"gh {' '.join(arguments[:3])} failed: {completed.stderr.strip()}")
    return completed.stdout


# --- what the report says per platform ----------------------------------------------------


def entries(report: dict) -> dict[str, dict]:
    """key -> {version, detail, level} for each platform the report covers."""
    found: dict[str, dict] = {}
    macos = report.get("macos", {})
    if macos.get("latest"):
        latest = macos["latest"]
        found["macos"] = {
            "version": f"{latest['version']} (build {latest['build']})",
            "detail": f"published {latest['published']}",
            "level": macos.get("level"),
            "extra": macos.get("builds_not_recorded", 0),
        }
    for architecture, entry in report.get("windows", {}).items():
        key = f"windows-{architecture}"
        if key in KEYS:
            found[key] = {
                "version": entry["latest"]["version"],
                "detail": f"`{entry['latest']['full_name']}`",
                "level": entry.get("level"),
                "extra": 0,
            }
    return found


def title_for(key: str, version: str) -> str:
    return f"[{NAMES[key]}] Official build {version} is not verified yet"


def body_for(key: str, entry: dict, canary_section: str = NO_CANARY) -> str:
    behind = ""
    if entry.get("extra", 0) > 1:
        behind = f" {entry['extra']} published builds are newer than the newest one the project has recorded."
    return f"""{marker(key)}
The newest official **{NAMES[key]}** build is not recorded by the project: `{entry['version']}` ({entry['detail']}).{behind}

{CANARY_BEGIN}
{canary_section}
{CANARY_END}

### What this means

The installers explain that the build is newer than the ones the project has recorded and ask before
building from it. Every patch step checks the app and stops by itself if it changed in a way the patch
does not cover, in which case nothing is installed and an existing router keeps working. This issue
tracks getting the build recorded (or ported, if the canary failed) so nobody is asked.

### To handle it

1. Read the canary result above (it installs the newest build on a clean machine and runs the real
   installer; on Windows it also boots the app).
2. **Passed:** record the build with `python scripts/record_build.py <the canary's result file>`, run the
   checks, and open a pull request ([docs/MAINTAINING.md](../blob/main/docs/MAINTAINING.md)).
3. **Failed:** the first error names what changed. Update the patch deliberately, with a test; never
   loosen an anchor to make a build pass.

An AI assistant can do either: ask it to handle this issue following `docs/MAINTAINING.md`.

_Updated automatically by `.github/workflows/upstream-watch.yml` and `upstream-canary.yml`._
"""


# --- finding and changing issues ---------------------------------------------------------------


def open_issues(gh: Gh) -> list[dict]:
    out = gh(["issue", "list", "--label", LABEL, "--state", "open", "--limit", "100", "--json", "number,title,body"])
    return json.loads(out or "[]")


def find(issues: list[dict], key: str) -> dict | None:
    return next((issue for issue in issues if marker(key) in (issue.get("body") or "")), None)


def canary_section_of(body: str) -> str:
    match = re.search(re.escape(CANARY_BEGIN) + r"\n(.*?)\n" + re.escape(CANARY_END), body or "", re.S)
    return match.group(1) if match else NO_CANARY


def ensure_label(gh: Gh) -> None:
    try:
        gh(["label", "create", LABEL, "--color", "5319e7", "--description", "A new official ChatGPT/Codex build to verify"])
    except RuntimeError:
        pass  # it already exists


def sync(report: dict, gh: Gh) -> list[str]:
    """Open, update or close each platform's issue; returns what was done."""
    done: list[str] = []
    issues = open_issues(gh)
    for key, entry in entries(report).items():
        issue = find(issues, key)
        if entry["level"] is not None:
            if issue:
                gh(["issue", "close", str(issue["number"]), "--comment",
                    f"The newest official {NAMES[key]} build, `{entry['version']}`, is now recorded ({entry['level']}). Closing."])
                done.append(f"closed #{issue['number']} ({key})")
            continue
        title = title_for(key, entry["version"])
        if issue is None:
            ensure_label(gh)
            gh(["issue", "create", "--title", title, "--body", body_for(key, entry), "--label", LABEL])
            done.append(f"opened an issue for {key}")
            continue
        body = body_for(key, entry, canary_section_of(issue["body"]))
        if issue["title"] != title:
            gh(["issue", "edit", str(issue["number"]), "--title", title, "--body", body])
            gh(["issue", "comment", str(issue["number"]), "--body",
                f"A newer official build is out: `{entry['version']}` ({entry['detail']})."])
            done.append(f"updated #{issue['number']} to {entry['version']} ({key})")
        elif body != issue["body"]:
            gh(["issue", "edit", str(issue["number"]), "--body", body])
            done.append(f"refreshed #{issue['number']} ({key})")
    return done


# --- the canary's result -------------------------------------------------------------------------


def canary_text(result: dict) -> str:
    build = result.get("package") or " ".join(
        part for part in (result.get("app_version"), f"(build {result['build']})" if result.get("build") else "") if part
    )
    link = f" ([run]({result['run_url']}))" if result.get("run_url") else ""
    verdicts = {
        "pass": "**passed**: every patch anchor matched" + (", and the patched app booted" if result.get("boot") == "ok" else ""),
        "fail": "**failed**: the patch or install stopped",
        "boot-failed": "**patched, but the app did not boot**",
        "error": "**could not run**",
    }
    lines = [f"Latest canary: {verdicts.get(result['status'], result['status'])} on `{build or 'unknown'}`{link}."]
    if result.get("message"):
        lines.append(f"> {result['message']}")
    if result.get("asar"):
        lines.append(f"app.asar `{result['asar']}`")
    if result["status"] == "pass":
        lines.append("Record it: `python scripts/record_build.py` with this run's result artifact (`canary-" + result["platform"] + ".json`).")
    return "\n".join(lines)


def apply_canary(results: list[dict], gh: Gh) -> list[str]:
    done: list[str] = []
    issues = open_issues(gh)
    for result in results:
        key = result["platform"]
        if key not in KEYS:
            continue
        section = canary_text(result)
        issue = find(issues, key)
        if issue is not None:
            body = issue["body"]
            replaced = re.sub(
                re.escape(CANARY_BEGIN) + r".*?" + re.escape(CANARY_END),
                lambda _: f"{CANARY_BEGIN}\n{section}\n{CANARY_END}",
                body,
                flags=re.S,
            )
            if replaced != body:
                gh(["issue", "edit", str(issue["number"]), "--body", replaced])
                done.append(f"updated the canary section of #{issue['number']} ({key})")
        elif result["status"] != "pass":
            ensure_label(gh)
            version = result.get("package") or result.get("app_version") or "latest"
            entry = {"version": version, "detail": "from the canary", "level": None, "extra": 0}
            title = f"[{NAMES[key]}] The canary cannot patch the newest official build ({version})"
            gh(["issue", "create", "--title", title, "--body", body_for(key, entry, section), "--label", LABEL])
            done.append(f"opened an issue for the failing canary on {key}")
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sync").add_argument("report", type=Path)
    commands.add_parser("canary").add_argument("results", nargs="+", type=Path)
    args = parser.parse_args(argv)
    if args.command == "sync":
        actions = sync(json.loads(args.report.read_text(encoding="utf-8")), real_gh)
    else:
        results = [json.loads(path.read_text(encoding="utf-8")) for path in args.results]
        actions = apply_canary(results, real_gh)
    print("\n".join(actions) if actions else "nothing to change")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
