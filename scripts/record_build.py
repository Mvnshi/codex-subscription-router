#!/usr/bin/env python3
"""Record an official build that the canary patched (and, on Windows, booted) successfully.

Takes one canary result file (the `canary-<platform>.json` artifact of a run of
.github/workflows/upstream-canary.yml, or the output of scripts/canary.py), adds the build to
scripts/recorded_builds.json as "verified", and regenerates the table between the markers in
docs/COMPATIBILITY.md. The patchers then accept the build without --allow-untested-source and
scripts/upstream_watch.py stops reporting it. Nothing else is touched: the hand-curated TESTED
tables stay a human decision.

    python scripts/record_build.py canary-windows-x64.json
    python scripts/record_build.py canary-macos.json --dry-run

Review the diff, run the checks, add a CHANGELOG line, and open a pull request
(docs/MAINTAINING.md has the whole routine).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "scripts" / "recorded_builds.json"
DOCS_FILE = ROOT / "docs" / "COMPATIBILITY.md"
TABLE_BEGIN = "<!-- recorded-builds:begin -->"
TABLE_END = "<!-- recorded-builds:end -->"
HASH = re.compile(r"^[0-9a-f]{64}$")
LEVEL_TEXT = {
    "tested": "tested by hand",
    "verified": "verified by the canary (patched with every anchor matching; on Windows also booted)",
}


class RecordError(Exception):
    """The result cannot be recorded; the message says why."""


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def record(result: dict, data: dict) -> str:
    """Add the build the result describes to data (in place); returns what was done."""
    if result.get("status") != "pass":
        raise RecordError(f"the canary result is '{result.get('status')}', not 'pass'; nothing to record")
    asar = result.get("asar", "")
    if not HASH.match(asar):
        raise RecordError("the result carries no app.asar hash")
    platform = result.get("platform", "")

    if platform == "macos":
        version, build = result.get("app_version", ""), result.get("build", "")
        if not version or not build:
            raise RecordError("the result carries no app version and build")
        key = f"{version}/{build}"
        existing = data["macos"]["builds"].get(key)
        if existing:
            if existing["asar"] != asar:
                raise RecordError(f"macOS {key} is recorded with a different app.asar hash; investigate before changing it")
            return f"macOS {key} was already recorded ({existing['level']})"
        data["macos"]["builds"][key] = {"level": "verified", "asar": asar}
        return f"recorded macOS {key} as verified"

    if platform in ("windows-x64", "windows-arm64"):
        architecture = platform.split("-", 1)[1]
        package = result.get("package", "")
        app, build = result.get("app_version", ""), result.get("build", "")
        host = [result.get("host_version", ""), result.get("host_second", "")]
        if not (package and app and build and all(host)):
            raise RecordError("the result lacks the Store package version, the app version and build, or the host version")
        packages = data["windows"]["packages"]
        entry = packages.get(package)
        if entry is None:
            packages[package] = {"app": app, "build": build, "level": "verified", "host": host, "asar": {architecture: asar}}
            return f"recorded OpenAI.Codex {package} ({architecture}) as verified"
        if entry["app"] != app or entry["build"] != build:
            raise RecordError(f"{package} is recorded as app {entry['app']} build {entry['build']}, not {app} build {build}")
        current = entry["asar"].get(architecture)
        if current == asar:
            return f"OpenAI.Codex {package} ({architecture}) was already recorded ({entry['level']})"
        if current is not None:
            raise RecordError(f"{package} ({architecture}) is recorded with a different app.asar hash; investigate before changing it")
        entry["asar"][architecture] = asar
        return f"added the {architecture} package of OpenAI.Codex {package} to the recorded builds"

    raise RecordError(f"unknown platform '{platform}'")


def render_table(data: dict) -> str:
    rows = []
    for package, entry in sorted(data["windows"]["packages"].items(), key=lambda item: version_key(item[0]), reverse=True):
        for architecture, digest in sorted(entry["asar"].items()):
            rows.append(
                f"| Windows {architecture} | `OpenAI.Codex` `{package}` | `{entry['app']}` / `{entry['build']}` | `{digest}` | {LEVEL_TEXT[entry['level']]} |"
            )
    for key, entry in sorted(data["macos"]["builds"].items(), key=lambda item: version_key(item[0]), reverse=True):
        version, build = key.split("/", 1)
        rows.append(f"| macOS | ChatGPT `{version}` | `{version}` / `{build}` | `{entry['asar']}` | {LEVEL_TEXT[entry['level']]} |")
    header = [
        "| Platform | Official build | App version / build | `app.asar` SHA-256 | How far it was checked |",
        "| --- | --- | --- | --- | --- |",
    ]
    return "\n".join(header + rows)


def update_docs(text: str, data: dict) -> str:
    if TABLE_BEGIN not in text or TABLE_END not in text:
        raise RecordError(f"{DOCS_FILE.name} has no {TABLE_BEGIN} ... {TABLE_END} section to fill")
    start = text.index(TABLE_BEGIN) + len(TABLE_BEGIN)
    end = text.index(TABLE_END)
    return text[:start] + "\n" + render_table(data) + "\n" + text[end:]


def write_lf(path: Path, text: str) -> None:
    """Write with LF endings on every OS (the repository stores LF; Path.write_text would give CRLF on Windows)."""
    path.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("result", type=Path, help="a canary result file")
    parser.add_argument("--dry-run", action="store_true", help="say what would change, change nothing")
    args = parser.parse_args(argv)
    try:
        result = json.loads(args.result.read_text(encoding="utf-8"))
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        message = record(result, data)
        docs = update_docs(DOCS_FILE.read_text(encoding="utf-8"), data)
    except (RecordError, OSError, ValueError, KeyError) as error:
        print(f"record_build: {error}", file=sys.stderr)
        return 1
    print(message)
    if args.dry_run:
        return 0
    data["windows"]["packages"] = dict(
        sorted(data["windows"]["packages"].items(), key=lambda item: version_key(item[0]))
    )
    write_lf(DATA_FILE, json.dumps(data, indent=2) + "\n")
    write_lf(DOCS_FILE, docs)
    print(f"updated {DATA_FILE.relative_to(ROOT)} and {DOCS_FILE.relative_to(ROOT)}")
    print("Next: run the checks, add a CHANGELOG line, and open a pull request (docs/MAINTAINING.md).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
