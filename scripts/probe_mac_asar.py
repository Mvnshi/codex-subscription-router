#!/usr/bin/env python3
"""Run the macOS patcher's asar-editing steps against an official ChatGPT build, on any OS.

A new official build usually breaks the patch in one of a few anchors, and the macOS patcher
only runs on a Mac. This downloads (or takes) the official zip, takes the app.asar out of it,
and runs the three steps that edit it (the Computer Use identity, the desktop profile and the
renderer anchors), reporting the first one that fails and why. The native parts of the macOS
patch (signing, helper layout, Info.plist) are not covered; the upstream canary runs those on a
real macOS runner.

    python scripts/probe_mac_asar.py --latest            # newest build in OpenAI's update feed
    python scripts/probe_mac_asar.py --zip ChatGPT.zip   # a zip you already have

It needs `npm ci --ignore-scripts` to have been run (for the asar tool) and about 3 GB of disk.
Extracting the archive is slow (several minutes). The work folder is removed afterwards unless
--keep is given. See docs/MAINTAINING.md.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
ROOT = SCRIPT_DIRECTORY.parent
sys.path.insert(0, str(SCRIPT_DIRECTORY))

import patch_app as mac  # noqa: E402
import upstream_watch  # noqa: E402

BUNDLE_RESOURCES = "ChatGPT.app/Contents/Resources/"
BUNDLE_PLIST = "ChatGPT.app/Contents/Info.plist"


def long_path(path: Path) -> str:
    """Windows has a 260-character limit unless the path carries the \\\\?\\ prefix."""
    text = str(path.resolve())
    return "\\\\?\\" + text if os.name == "nt" and not text.startswith("\\\\?\\") else text


def place_files(zip_path: Path, destination: Path) -> dict:
    """Put app.asar, its unpacked modules and the Info.plist under destination; returns the plist."""
    with zipfile.ZipFile(zip_path) as archive:
        wanted = [
            name
            for name in archive.namelist()
            if not name.endswith("/")
            and (
                name == BUNDLE_PLIST
                or name == BUNDLE_RESOURCES + "app.asar"
                or name.startswith(BUNDLE_RESOURCES + "app.asar.unpacked/")
            )
        ]
        if BUNDLE_RESOURCES + "app.asar" not in wanted:
            raise SystemExit(f"{zip_path} does not contain {BUNDLE_RESOURCES}app.asar")
        for name in wanted:
            relative = "Info.plist" if name == BUNDLE_PLIST else name[len(BUNDLE_RESOURCES):]
            target = destination / relative
            os.makedirs(long_path(target.parent), exist_ok=True)
            with archive.open(name) as source, open(long_path(target), "wb") as sink:
                shutil.copyfileobj(source, sink)
    with open(destination / "Info.plist", "rb") as handle:
        return plistlib.load(handle)


def download_latest(destination: Path) -> Path:
    builds = upstream_watch.parse_appcast(upstream_watch.fetch_text(upstream_watch.APPCAST_URL))
    if not builds:
        raise SystemExit("the update feed lists no builds")
    latest = builds[0]
    print(f"Downloading {latest.version} (build {latest.build}) ...")
    target = destination / "ChatGPT.zip"
    request = upstream_watch.urllib.request.Request(latest.url, headers={"User-Agent": upstream_watch.USER_AGENT})
    with upstream_watch.urllib.request.urlopen(request, timeout=120) as response, open(target, "wb") as sink:
        shutil.copyfileobj(response, sink)
    return target


def run_steps(asar: Path, extracted: Path, version: str, build: str) -> bool:
    node_asar = ROOT / "node_modules" / "@electron" / "asar" / "bin" / "asar.mjs"
    if not node_asar.is_file():
        raise SystemExit("run `npm ci --ignore-scripts` first (the asar tool is missing)")
    print("Extracting app.asar (this takes a few minutes) ...")
    subprocess.run(["node", str(node_asar), "extract", str(asar), str(extracted)], check=True)
    key = (version, build)
    expected = mac.EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD.get(key, mac.EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS)
    print(f"Build {version} ({build}): the patcher expects {expected} Computer Use references in the archive.")
    steps = [
        ("patch_asar_computer_use_identity", lambda: mac.patch_asar_computer_use_identity(extracted, expected)),
        ("patch_desktop_profile", lambda: mac.patch_desktop_profile(extracted, Path.home() / "Applications" / mac.COMPUTER_USE_APP_NAME)),
        ("patch_renderer", lambda: mac.patch_renderer(extracted, "0" * 64)),
    ]
    for name, step in steps:
        try:
            step()
        except Exception as error:  # noqa: BLE001 - the point is to report whatever the step says
            print(f"FAIL  {name}: {error}")
            return False
        print(f"PASS  {name}")
    print("Every asar step passed. The native steps (signing, helper layout) still run only on macOS.")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--latest", action="store_true", help="download the newest build from the update feed")
    source.add_argument("--zip", type=Path, help="an official ChatGPT-darwin-arm64 zip you already have")
    parser.add_argument("--keep", action="store_true", help="keep the work folder")
    args = parser.parse_args(argv)

    # A short folder: the unpacked modules nest deeply, and Windows paths are limited.
    base = Path(tempfile.gettempdir()).anchor or tempfile.gettempdir()
    work = Path(tempfile.mkdtemp(prefix="csr-probe-", dir=base))
    try:
        zip_path = download_latest(work) if args.latest else args.zip
        placed = work / "r"
        placed.mkdir()
        info = place_files(zip_path, placed)
        version = str(info.get("CFBundleShortVersionString", "unknown"))
        build = str(info.get("CFBundleVersion", "unknown"))
        ok = run_steps(placed / "app.asar", work / "asar", version, build)
        return 0 if ok else 1
    finally:
        if args.keep:
            print(f"Work folder kept: {work}")
        else:
            shutil.rmtree(long_path(work), ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
