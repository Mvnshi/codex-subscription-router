#!/usr/bin/env python3
"""Compare the newest official ChatGPT/Codex builds with the ones this project has recorded.

OpenAI ships a new build of the desktop app roughly every day, for macOS through a
Sparkle appcast and for Windows through the Microsoft Store. This reads both public
feeds (no download of the app itself), looks every build up in the tables the patchers
keep, and reports what is new. The scheduled workflow
.github/workflows/upstream-watch.yml turns that report into one tracking issue per
platform; .github/workflows/upstream-canary.yml then installs the newest build on a
clean machine and tries to patch it.

    python scripts/upstream_watch.py                  # a readable report
    python scripts/upstream_watch.py --json           # the same, machine-readable
    python scripts/upstream_watch.py --check          # exit 1 when a newest build is unrecorded

Standard library only. The feeds are fetched by two small functions that tests replace.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from xml.etree import ElementTree

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

import patch_app  # noqa: E402  (macOS patcher: its recorded builds)
import patch_app_windows  # noqa: E402  (Windows patcher: its recorded builds)

APPCAST_URL = "https://persistent.oaistatic.com/codex-app-prod/appcast.xml"
# The Microsoft Store listing of the app (product 9PLM9XGG6VKS, package family
# OpenAI.Codex_2p2nqsd0c76g0). The display catalog is public and needs no sign-in.
STORE_PRODUCT_ID = "9PLM9XGG6VKS"
STORE_CATALOG_URL = (
    "https://displaycatalog.mp.microsoft.com/v7.0/products"
    f"?bigIds={STORE_PRODUCT_ID}&market=US&languages=en-US"
)
SPARKLE = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"
USER_AGENT = "codex-subscription-router upstream-watch (+https://github.com/Mvnshi/codex-subscription-router)"

RECORDED_TESTED = "tested"
RECORDED_VERIFIED = "verified"


@dataclass(frozen=True)
class MacBuild:
    version: str
    build: str
    published: str
    url: str


@dataclass(frozen=True)
class StorePackage:
    full_name: str
    version: str
    architecture: str


# --- fetching ---------------------------------------------------------------


def fetch_text(url: str, *, timeout: float = 30.0) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8")


# --- parsing ------------------------------------------------------------------


def parse_appcast(text: str) -> list[MacBuild]:
    """Every item of the appcast, newest first as published."""
    root = ElementTree.fromstring(text)
    builds = []
    for item in root.iter("item"):
        enclosure = item.find("enclosure")
        version = item.findtext(f"{SPARKLE}shortVersionString") or item.findtext("title") or ""
        build = item.findtext(f"{SPARKLE}version") or ""
        if not version or not build or enclosure is None:
            continue
        builds.append(
            MacBuild(
                version=version.strip(),
                build=build.strip(),
                published=(item.findtext("pubDate") or "").strip(),
                url=enclosure.get("url", ""),
            )
        )
    return builds


PACKAGE_FULL_NAME = re.compile(r"^OpenAI\.Codex_(?P<version>\d+(?:\.\d+){3})_(?P<arch>[a-z0-9]+)__")


def parse_store_catalog(text: str) -> list[StorePackage]:
    """The distinct MSIX packages the Store currently offers for the app."""
    data = json.loads(text)
    packages: dict[str, StorePackage] = {}
    for product in data.get("Products", []):
        for availability in product.get("DisplaySkuAvailabilities", []):
            for package in availability.get("Sku", {}).get("Properties", {}).get("Packages", []):
                full_name = package.get("PackageFullName", "")
                match = PACKAGE_FULL_NAME.match(full_name)
                if match:
                    packages[full_name] = StorePackage(full_name, match["version"], match["arch"])
    return sorted(packages.values(), key=lambda p: (version_key(p.version), p.architecture))


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


# --- what the project has recorded ------------------------------------------------


def recorded_macos() -> dict[tuple[str, str], str]:
    """(version, build) -> how far the build was checked."""
    recorded = {key: RECORDED_VERIFIED for key in patch_app.CANARY_VERIFIED_SOURCE_BUILDS}
    recorded.update({key: RECORDED_TESTED for key in patch_app.TESTED_SOURCE_BUILDS})
    return recorded


def recorded_windows() -> dict[tuple[str, str], str]:
    """(store package version, architecture) -> how far the build was checked."""
    return {
        (package, architecture): record["level"]
        for package, record in patch_app_windows.WINDOWS_PACKAGE_RECORDS.items()
        for architecture in record["asar"]
    }


# --- the report -----------------------------------------------------------------------


def evaluate(
    macos_builds: list[MacBuild],
    store_packages: list[StorePackage],
    macos_recorded: dict[tuple[str, str], str] | None = None,
    windows_recorded: dict[tuple[str, str], str] | None = None,
) -> dict:
    macos_recorded = recorded_macos() if macos_recorded is None else macos_recorded
    windows_recorded = recorded_windows() if windows_recorded is None else windows_recorded
    needs_attention: list[str] = []

    macos: dict = {"latest": None, "level": None, "builds_not_recorded": 0}
    if macos_builds:
        latest = macos_builds[0]
        level = macos_recorded.get((latest.version, latest.build))
        newest_recorded = next(
            (index for index, build in enumerate(macos_builds) if (build.version, build.build) in macos_recorded), None
        )
        macos = {
            "latest": asdict(latest),
            "level": level,
            # How many published builds are newer than the newest one that is recorded.
            "builds_not_recorded": len(macos_builds) if newest_recorded is None else newest_recorded,
            "feed_builds": len(macos_builds),
        }
        if level is None:
            needs_attention.append("macos")

    windows: dict = {}
    for architecture in sorted({package.architecture for package in store_packages}):
        packages = [p for p in store_packages if p.architecture == architecture]
        latest = max(packages, key=lambda p: version_key(p.version))
        level = windows_recorded.get((latest.version, architecture))
        windows[architecture] = {"latest": asdict(latest), "level": level}
        if level is None:
            needs_attention.append(f"windows-{architecture}")

    return {"macos": macos, "windows": windows, "needs_attention": needs_attention}


def describe(level: str | None) -> str:
    return {
        RECORDED_TESTED: "recorded, tested",
        RECORDED_VERIFIED: "recorded, verified by the canary (patched and booted)",
        None: "NOT RECORDED",
    }[level]


def markdown(report: dict) -> str:
    lines = ["| Platform | Newest official build | Status |", "| --- | --- | --- |"]
    macos = report["macos"]
    if macos.get("latest"):
        latest = macos["latest"]
        status = describe(macos["level"])
        if macos["level"] is None and macos["builds_not_recorded"] > 1:
            status += f" ({macos['builds_not_recorded']} published builds are newer than the newest recorded one)"
        lines.append(f"| macOS | `{latest['version']}` (build `{latest['build']}`, {latest['published']}) | {status} |")
    else:
        lines.append("| macOS | feed unavailable | unknown |")
    for architecture, entry in report["windows"].items():
        lines.append(
            f"| Windows {architecture} (Microsoft Store) | `OpenAI.Codex` `{entry['latest']['version']}` | {describe(entry['level'])} |"
        )
    if not report["windows"]:
        lines.append("| Windows (Microsoft Store) | catalog unavailable | unknown |")
    return "\n".join(lines) + "\n"


def gather(fetch=None) -> dict:
    """Fetch both feeds and evaluate them. A feed that cannot be read is reported, not hidden."""
    fetch = fetch or fetch_text  # looked up now, so tests can replace fetch_text
    errors = []
    macos_builds: list[MacBuild] = []
    store_packages: list[StorePackage] = []
    try:
        macos_builds = parse_appcast(fetch(APPCAST_URL))
    except Exception as error:  # noqa: BLE001 - reported in the output, never swallowed silently
        errors.append(f"macOS appcast: {error}")
    try:
        store_packages = parse_store_catalog(fetch(STORE_CATALOG_URL))
    except Exception as error:  # noqa: BLE001
        errors.append(f"Microsoft Store catalog: {error}")
    report = evaluate(macos_builds, store_packages)
    report["errors"] = errors
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--check", action="store_true", help="exit 1 when a newest build is not recorded")
    args = parser.parse_args(argv)
    report = gather()
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(markdown(report), end="")
        for error in report["errors"]:
            print(f"warning: {error}", file=sys.stderr)
    if report["errors"] and not (report["macos"].get("latest") or report["windows"]):
        return 2  # nothing could be read at all
    return 1 if args.check and report["needs_attention"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
