#!/usr/bin/env python3
"""Create an independent, profile-isolated copy of the official ChatGPT desktop app for Windows with Codex multiplexing.

PROVISIONAL. One build of the official app has been exercised with this patcher,
the Microsoft Store package OpenAI.Codex 26.930.3930.0 (see
TESTED_WINDOWS_SOURCE_BUILDS and docs/COMPATIBILITY.md for what it has and has
not passed); any other build needs --allow-untested-source. The official app is
only ever distributed through the Microsoft Store, so a Store package is the
normal source: it is found through the package registry and its app directory is
copied read-only. Every assumption about the Windows layout (where the install
lives, which executable is the host, where the bundled codex.exe sits, which
files stay unpacked, whether the executable carries an embedded asar integrity
resource) is checked at run time with exact, fail-closed checks rather than
assumed, and the checks and their outcomes are recorded in docs/WINDOWS.md.

Differences from the macOS patcher (scripts/patch_app.py), on purpose:
- Nothing is code-signed. Windows has no codesign step; rewriting the
  INTEGRITY/ELECTRONASAR resource of the copied Electron executable drops
  its Authenticode signature, so the copy runs unsigned (SmartScreen may
  warn once).
- Computer Use is not patched. The Windows helper is started by the app's
  own node runtime as a child process over stdio, so there is no service,
  socket or identity to re-point; the copy follows whatever the official app
  does (the feature is off unless CODEX_ELECTRON_ENABLE_WINDOWS_COMPUTER_USE=1
  is set in the environment).
- The launcher is a Go program (cmd/codex-router-launcher) built as
  "Codex Subscription Router.exe" beside the Electron executable.
- The URL scheme is retargeted in the main-process bundles (Windows registers
  protocol handlers at run time from JavaScript, not from an Info.plist).
"""

from __future__ import annotations

import argparse
import dataclasses
import errno
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path, PureWindowsPath
from typing import NamedTuple
from xml.etree import ElementTree

# The module is both a script (python scripts\patch_app_windows.py) and an
# import target for the tests, which discover from the scripts directory.
SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

# Imported under an alias: this module's own orchestration entry point is
# also called patch_app, mirroring the macOS module's public name.
import patch_app as shared  # noqa: E402  (shared macOS/Windows logic)


DESTINATION_DIRECTORY_NAME = "Codex Subscription Router"
LAUNCHER_EXECUTABLE_NAME = "Codex Subscription Router.exe"
REAL_CODEX_EXECUTABLE_NAME = "codex.real.exe"
CODEX_EXECUTABLE_NAME = "codex.exe"
# The "file" key electron/packager writes into the INTEGRITY/ELECTRONASAR
# resource for the main archive; compared case-insensitively with either
# separator because the packager has emitted both forms over time.
INTEGRITY_ASAR_FILE = "resources\\app.asar"
PROTOCOL_SCHEME = "codex-subscription-router"
DEFAULT_ELECTRON_EXECUTABLE_NAME = "ChatGPT.exe"
# What people look for in the Start menu and on the Desktop. The install folder
# and the launcher keep the longer project name so existing installs, backups
# and state are untouched.
SHORTCUT_DISPLAY_NAME = "Codex Router"
SHORTCUT_DESCRIPTION = "Codex with all your subscriptions"
START_MENU_SHORTCUT_NAME = f"{SHORTCUT_DISPLAY_NAME}.lnk"
# The shortcut earlier versions created; removed when it points at this launcher.
LEGACY_SHORTCUT_NAME = f"{DESTINATION_DIRECTORY_NAME}.lnk"
# The router's own artwork (scripts/make_icons.py) so the copy can be told apart
# from the official app in the Start menu, taskbar and notification area. The app
# reads its window and tray icons from plain files beside app.asar, so the copy
# gets ours under the names it looks up. Names the official app does not ship are
# skipped, not created.
ICON_DIRECTORY = shared.PROJECT_ROOT / "assets" / "windows"
SHORTCUT_ICON_NAME = f"{SHORTCUT_DISPLAY_NAME}.ico"
ICON_REPLACEMENTS = (
    ("codex-router.ico", ("chatgpt-app-dark.ico", "chatgpt-app-light.ico")),
    ("codex-router-tray-dark.ico", ("chatgpt-tray-dark.ico",)),
    ("codex-router-tray-light.ico", ("chatgpt-tray-light.ico",)),
)
# Go's GOARCH for the machine type of the official host executable, as
# scripts/win/exe-info.mjs reports it. The multiplexer and launcher run next to
# that host, so they are built for its architecture rather than whatever the
# machine's Go happens to default to (an emulated x64 toolchain on an ARM64 PC
# would otherwise produce x64 programs).
GO_ARCHITECTURES = {"x64": "amd64", "arm64": "arm64"}
LONG_PATHS_REGISTRY_KEY = (
    "HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled"
)
# Windows MAX_PATH; paths at or beyond it need LongPathsEnabled=1 (and a
# long-path-aware Python, which python.org builds are).
MAX_PATH = 260
# Slack for the renames the patcher performs on the copied tree
# (codex.exe -> codex.real.exe adds five characters) and for the trailing NUL.
PATH_LENGTH_MARGIN = 8
# Win32 ERROR_NOT_SAME_DEVICE: what MoveFileEx reports for a rename across
# volumes. Python maps it to errno.EXDEV as well; move_directory checks both
# so the fallback does not hinge on that mapping.
ERROR_NOT_SAME_DEVICE = 17
# tempfile.mkdtemp appends eight random characters to the prefix. Both names are
# deliberately short: the staged copy of the official app is the longest tree the
# install writes, and the default Windows configuration has long paths off
# (MAX_PATH 260), so every character here counts against the app's deepest paths.
STAGING_PREFIX = ".csr-"
STAGING_RANDOM_LENGTH = 8
STAGING_DIRECTORY_NAME = "app"
# AppxManifest.xml Identity Name of the official Microsoft Store package. The
# Store build is the only way OpenAI ships the Windows desktop app.
STORE_PACKAGE_NAME = "OpenAI.Codex"
STORE_MANIFEST_NAME = "AppxManifest.xml"
EXE_INFO_SCRIPT = shared.PROJECT_ROOT / "scripts" / "win" / "exe-info.mjs"
SET_ASAR_INTEGRITY_SCRIPT = (
    shared.PROJECT_ROOT / "scripts" / "win" / "set-asar-integrity.mjs"
)
ASAR_CLI = (
    shared.PROJECT_ROOT / "node_modules" / "@electron" / "asar" / "bin" / "asar.mjs"
)

# Keyed by (versionInfo.productVersion, versionInfo.fileVersion) of the host
# executable -> sha256 (or a tuple of sha256 values) of the whole app.asar,
# mirroring patch_app.TESTED_SOURCE_BUILDS. On the Store build the host is
# Chromium's chrome.exe launcher, so the key is the Chromium runtime version,
# which several app builds can share; the app.asar hash is the real identity,
# hence more than one hash per key is allowed.
#
# Every entry is PROVISIONAL until docs/SMOKE-TEST.md has been completed in
# full on it; docs/COMPATIBILITY.md says what each one has actually passed.
TESTED_WINDOWS_SOURCE_BUILDS: dict[tuple[str, str], str | tuple[str, ...]] = {
    # OpenAI.Codex 26.930.3930.0 (Microsoft Store, x64): app 26.930.31730,
    # build 12947, Chromium 154.0.8037.98.
    ("154.0.8037.98", "154.0.8037.98"): (
        "af98213984ec4556778ef9276193d51460153fb9b30fded882d503637b84abba"
    ),
}

# Top-level executables that are never the Electron host: Squirrel's
# uninstaller/updater stubs and NSIS uninstallers. Matched case-insensitively
# with fnmatch against the file name.
EXCLUDED_EXECUTABLE_PATTERNS = (
    "uninstall*",
    "unins*",
    "update.exe",
    "squirrel*.exe",
    "elevate.exe",
)

# (environment variable, relative path parts). A trailing "app-*" part is a
# Squirrel version directory; the newest version wins for that template.
SOURCE_CANDIDATE_TEMPLATES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("LOCALAPPDATA", ("Programs", "ChatGPT")),
    ("LOCALAPPDATA", ("Programs", "Codex")),
    ("LOCALAPPDATA", ("ChatGPT", "app-*")),
    ("LOCALAPPDATA", ("Codex", "app-*")),
    ("ProgramFiles", ("ChatGPT",)),
    ("ProgramFiles", ("Codex",)),
)

PROTOCOL_CALL_PATTERN = re.compile(
    r"(setAsDefaultProtocolClient|removeAsDefaultProtocolClient|isDefaultProtocolClient)"
    r"\((['\"`])codex\2"
)
# Any remaining mention of the one API that writes the registry which is not
# immediately the retargeted literal call: a variable or constant argument, a
# different literal, .call/.apply, optional chaining, or an alias. Checked
# after retargeting; removeAsDefaultProtocolClient only deletes keys that
# already point at the calling executable and isDefaultProtocolClient only
# reads, so those two are not registrations and are left to the warning.
PROTOCOL_REGISTRATION_RESIDUE_PATTERN = re.compile(
    r"\bsetAsDefaultProtocolClient\b(?!\((['\"`])" + re.escape(PROTOCOL_SCHEME) + r"\1)"
)
# The Store build declares codex:// in its package manifest and its own
# run-time registration returns early on Windows:
#   function w(){if(process.platform===`win32`)return;let t=Q7(e.isPackaged);
#   try{e.setAsDefaultProtocolClient(t)||...}
# That one call is unreachable on win32, so it cannot write HKCU\Software\Classes
# and needs no retargeting. Only this exact shape is accepted (the platform
# guard must be the first statement before the scheme is computed and the call
# is made); every other residue still stops the patch.
WINDOWS_UNREACHABLE_REGISTRATION_PATTERN = re.compile(
    r"if\(process\.platform===(['\"`])win32\1\)return;"
    r"let ([\w$]+)=[\w$]+\([\w$]+\.isPackaged\);"
    r"try\{[\w$]+\.setAsDefaultProtocolClient\(\2\)"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {shared.PROJECT_VERSION}"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Official install directory (default: discover the single qualifying install).",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=None,
        help="Install directory (default: %%LOCALAPPDATA%%\\Programs\\Codex Subscription Router).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing destination after moving it to a timestamped backup.",
    )
    parser.add_argument(
        "--allow-untested-source",
        action="store_true",
        help="Continue after an explicit version, build, or ASAR hash mismatch.",
    )
    parser.add_argument(
        "--electron-executable",
        metavar="NAME",
        default=None,
        help="File name of the Electron executable in the source directory (default: discover).",
    )
    parser.add_argument(
        "--codex-executable",
        metavar="RELPATH",
        default=None,
        help="Path of the bundled codex.exe relative to the source directory (default: discover).",
    )
    parser.add_argument(
        "--no-shortcut",
        action="store_true",
        help="Do not create the Start menu or Desktop shortcut.",
    )
    parser.add_argument(
        "--check-source",
        action="store_true",
        help=(
            "Only find the official app and check it against the recorded builds, then stop "
            "without copying or building anything (exit 3: not a recorded build)."
        ),
    )
    parser.add_argument(
        "--no-desktop-shortcut",
        action="store_true",
        help="Create the Start menu shortcut but not the Desktop one.",
    )
    return parser.parse_args(argv)


# --- pure helpers -----------------------------------------------------------


def environment_value(env: Mapping[str, str], name: str) -> str | None:
    """Case-insensitive lookup so plain dicts behave like os.environ on Windows."""
    value = env.get(name)
    if value is not None:
        return value or None
    lowered = name.lower()
    for key, candidate in env.items():
        if key.lower() == lowered:
            return candidate or None
    return None


def default_destination(env: Mapping[str, str]) -> Path:
    local_app_data = environment_value(env, "LOCALAPPDATA")
    if local_app_data is None:
        raise RuntimeError("LOCALAPPDATA is not set; pass --destination")
    return Path(local_app_data) / "Programs" / DESTINATION_DIRECTORY_NAME


def squirrel_version_key(path: Path) -> tuple[int, tuple[int, ...], str]:
    """Sort key: parsable app-<version> directories first, newest first."""
    match = re.fullmatch(r"app-(\d+(?:\.\d+)*)", path.name)
    if match is None:
        return (1, (), path.name)
    return (0, tuple(-int(part) for part in match.group(1).split(".")), path.name)


def candidate_source_directories(env: Mapping[str, str]) -> list[Path]:
    """Expand SOURCE_CANDIDATE_TEMPLATES to the directories that exist.

    For a Squirrel template only the newest app-* directory is a candidate:
    Squirrel keeps the previous version beside the current one for rollback,
    and copying the stale one would silently install an older app.
    """
    candidates: list[Path] = []
    for variable, parts in SOURCE_CANDIDATE_TEMPLATES:
        base = environment_value(env, variable)
        if base is None:
            continue
        root = Path(base)
        if parts[-1] == "app-*":
            parent = root.joinpath(*parts[:-1])
            if not parent.is_dir():
                continue
            versions = sorted(
                (entry for entry in parent.glob("app-*") if entry.is_dir()),
                key=squirrel_version_key,
            )
            if versions:
                candidates.append(versions[0])
            continue
        candidate = root.joinpath(*parts)
        if candidate.is_dir():
            candidates.append(candidate)
    return candidates


def is_electron_app_directory(path: Path) -> bool:
    return path.is_dir() and (path / "resources" / "app.asar").is_file()


def is_excluded_executable(name: str) -> bool:
    lowered = name.lower()
    return any(
        fnmatch.fnmatchcase(lowered, pattern) for pattern in EXCLUDED_EXECUTABLE_PATTERNS
    )


def electron_executables(path: Path) -> list[Path]:
    """Top-level .exe files that could be the Electron host, sorted by name."""
    return sorted(
        entry
        for entry in path.iterdir()
        if entry.is_file()
        and entry.suffix.lower() == ".exe"
        and not is_excluded_executable(entry.name)
        and entry.name.lower() != LAUNCHER_EXECUTABLE_NAME.lower()
    )


def is_store_install(path: Path) -> bool:
    """True for an install inside a Microsoft Store/MSIX package (WindowsApps)."""
    return any(part.lower() == "windowsapps" for part in path.parts)


def version_key(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


def store_install_candidates() -> list[Path]:
    """The app directory of the newest installed Store package, as a list.

    OpenAI ships the Windows desktop app only through the Microsoft Store, as
    the MSIX package OpenAI.Codex. Its files sit under
    %ProgramFiles%\\WindowsApps, a directory a standard user can read file by
    file but cannot enumerate, so the package registry is asked where it lives
    (no elevation needed). The package's own executable and resources are in its
    "app" subdirectory. A failed lookup means there is no Store candidate, not
    an error: other installs are still discovered.
    """
    script = (
        f"Get-AppxPackage -Name {powershell_literal(STORE_PACKAGE_NAME)} | "
        "ForEach-Object { $_.Version.ToString() + '|' + $_.InstallLocation }"
    )
    try:
        result = run_helper(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            "package lookup (Get-AppxPackage)",
            errors="replace",
        )
    except (RuntimeError, OSError):
        return []
    packages = []
    for line in result.stdout.splitlines():
        version, separator, location = line.strip().partition("|")
        if separator and location:
            packages.append((version_key(version), Path(location) / "app"))
    packages.sort(key=lambda entry: entry[0], reverse=True)
    return [directory for _, directory in packages[:1]]


def manifest_executable_name(app_dir: Path) -> str | None:
    """The host executable the Store manifest declares for the main application.

    The package manifest sits beside the app directory and names the first
    Application's Executable (app/ChatGPT.exe), which is exact where guessing
    among the Chromium helper executables beside it (chrome_proxy.exe,
    elevation_service.exe, ...) is not. Anything unexpected returns None and the
    caller falls back to discovery.
    """
    manifest = app_dir.parent / STORE_MANIFEST_NAME
    if not manifest.is_file():
        return None
    try:
        root = ElementTree.parse(manifest).getroot()
    except (ElementTree.ParseError, OSError):
        return None
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "Application":
            continue
        declared = element.get("Executable")
        if not declared:
            return None
        target = app_dir.parent / PureWindowsPath(declared.replace("/", "\\"))
        if target.parent != app_dir:
            return None
        return target.name
    return None


def discover_source(
    env: Mapping[str, str],
    explicit: Path | None,
    store_candidates: Sequence[Path] = (),
) -> Path:
    if explicit is not None:
        if not is_electron_app_directory(explicit):
            raise RuntimeError(
                f"not an Electron app directory (no resources\\app.asar): {explicit}"
            )
        return explicit
    examined = [*candidate_source_directories(env), *store_candidates]
    qualifying = [candidate for candidate in examined if is_electron_app_directory(candidate)]
    if len(qualifying) != 1:
        examined_text = ", ".join(str(candidate) for candidate in examined) or "(none)"
        hint = (
            ". Install the ChatGPT/Codex desktop app from the Microsoft Store first, "
            "then rerun; or pass --source"
            if not qualifying
            else "; pass --source"
        )
        raise RuntimeError(
            f"expected exactly one official install, found {len(qualifying)} "
            f"(examined: {examined_text}){hint}"
        )
    return qualifying[0]


def require_bare_file_name(name: str, what: str) -> None:
    # The launcher only ever starts a sibling file, and -X ldflags cannot
    # carry whitespace or quotes; reject anything that is not a plain name.
    if not name or name in {".", ".."} or any(separator in name for separator in "/\\"):
        raise RuntimeError(f"{what} must be a bare file name, got {name!r}")


def select_electron_executable(app_dir: Path, override: str | None) -> Path:
    if override is not None:
        require_bare_file_name(override, "--electron-executable")
        candidate = app_dir / override
        if not candidate.is_file():
            raise RuntimeError(f"Electron executable not found: {candidate}")
        return candidate
    declared = manifest_executable_name(app_dir)
    if declared is not None and (app_dir / declared).is_file():
        return app_dir / declared
    executables = electron_executables(app_dir)
    if len(executables) != 1:
        names = ", ".join(entry.name for entry in executables) or "(none)"
        raise RuntimeError(
            f"expected one Electron executable in {app_dir}, found {len(executables)} "
            f"({names}); pass --electron-executable NAME"
        )
    return executables[0]


def locate_codex_executable(app_dir: Path, override: str | None) -> Path:
    if override is not None:
        # The multiplexer is copied over the result and the original renamed
        # beside it, so the result must be unreachable outside the staged
        # copy: pathlib discards the left operand when the right one is
        # anchored (an absolute path, a drive, a leading separator, a UNC
        # share) and keeps ".." for the OS to resolve, either of which would
        # patch the official install in place. PureWindowsPath so every kind
        # of Windows anchor is recognised on any host, including the tests.
        relative = PureWindowsPath(override)
        if (
            not relative.parts
            or relative.anchor
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            raise RuntimeError(
                "--codex-executable must be a relative path inside the source "
                f"directory, got {override!r}"
            )
        if relative.name.lower() != CODEX_EXECUTABLE_NAME:
            raise RuntimeError(
                f"--codex-executable must name {CODEX_EXECUTABLE_NAME} (the "
                f"multiplexer is copied over it), got {override!r}"
            )
        candidate = app_dir / override
        if not candidate.resolve().is_relative_to(app_dir.resolve()):
            raise RuntimeError(f"--codex-executable escapes the source directory: {override!r}")
        if not candidate.is_file():
            raise RuntimeError(f"bundled Codex executable not found: {candidate}")
        return candidate
    excluded = {REAL_CODEX_EXECUTABLE_NAME.lower(), LAUNCHER_EXECUTABLE_NAME.lower()}
    matches = sorted(
        entry
        for entry in app_dir.rglob("*")
        if entry.is_file()
        and entry.name.lower() == CODEX_EXECUTABLE_NAME
        and entry.name.lower() not in excluded
    )
    if len(matches) > 1:
        # The engine lives under resources; a same-named file beside the host
        # executable (the Store package ships a 20 KB Codex.exe stub there) is
        # not it. Narrow only when that leaves exactly one.
        in_resources = [
            entry
            for entry in matches
            if entry.relative_to(app_dir).parts[0].lower() == "resources"
        ]
        if len(in_resources) == 1:
            matches = in_resources
    if len(matches) != 1:
        found = ", ".join(str(entry.relative_to(app_dir)) for entry in matches) or "(none)"
        raise RuntimeError(
            f"expected one bundled {CODEX_EXECUTABLE_NAME} under {app_dir}, found "
            f"{len(matches)} ({found}); pass --codex-executable RELPATH"
        )
    return matches[0]


class UnpackPatterns(NamedTuple):
    """The asar pack options that reproduce the official unpacked set."""

    # asar pack --unpack: loose files, matched by base name (matchBase).
    unpack: str | None
    # asar pack --unpack-dir: directories kept unpacked with everything in them.
    unpack_dir: str | None


# Characters minimatch would interpret (or that would split a brace group); an
# unpacked path containing one cannot be handed to asar as a literal pattern.
PATTERN_SPECIAL_CHARACTERS = re.compile(r"[{}\[\]()*?!|,\\]")


def brace_pattern(alternatives: list[str]) -> str | None:
    if not alternatives:
        return None
    if len(alternatives) == 1:
        # minimatch does not expand a single-alternative brace group.
        return alternatives[0]
    return "{" + ",".join(alternatives) + "}"


def unpack_patterns(listing_text: str) -> UnpackPatterns:
    """Derive asar's pack options from the official `asar list --is-pack` output.

    The official archive keeps a precise set unpacked: whole directories
    (better-sqlite3's build and lib, node-pty's build and lib) and a few loose
    native files whose directories stay packed (a nested package's .node
    binaries). Unpacking whole top-level packages instead would also unpack
    every packed file inside them, including deeply nested JavaScript, and the
    resulting directory paths exceed what Windows allows with long paths off.
    The top-most unpacked directories become --unpack-dir patterns, the
    remaining unpacked files become --unpack patterns by base name (a path
    pattern would have to survive minimatch's rule that "**" does not cross the
    dot-directory the staging copy lives in); verify_unpacked_identical then
    proves the repacked archive matches the official set exactly.
    """
    all_paths, unpacked = parse_asar_listing(listing_text)
    parents = {path.rpartition("/")[0] for path in all_paths}
    unpacked_directories = {path for path in unpacked if path in parents}

    def inside_unpacked_directory(path: str) -> bool:
        parent = path.rpartition("/")[0]
        while parent:
            if parent in unpacked_directories:
                return True
            parent = parent.rpartition("/")[0]
        return False

    top_directories = sorted(
        path for path in unpacked_directories if not inside_unpacked_directory(path)
    )
    loose_files = sorted(
        path
        for path in unpacked - unpacked_directories
        if not inside_unpacked_directory(path)
    )
    directory_patterns = [path.lstrip("/") for path in top_directories]
    file_patterns = sorted({path.rpartition("/")[2] for path in loose_files})
    for pattern in (*directory_patterns, *file_patterns):
        if PATTERN_SPECIAL_CHARACTERS.search(pattern):
            raise RuntimeError(
                "the official archive unpacks a path containing characters asar "
                f"would treat as a pattern, which this patcher cannot reproduce: {pattern}"
            )
    return UnpackPatterns(brace_pattern(file_patterns), brace_pattern(directory_patterns))


def parse_asar_listing(text: str) -> tuple[set[str], set[str]]:
    """Split `asar list --is-pack` output into (all paths, unpacked paths).

    Lines look like "unpack : /a/b" or "pack   : /a/b"; on Windows asar joins
    with backslashes, so separators are normalised to "/".
    """
    all_paths: set[str] = set()
    unpacked: set[str] = set()
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        state, separator, rest = line.partition(" : ")
        if not separator:
            raise RuntimeError(f"unexpected asar list --is-pack line: {raw_line!r}")
        state = state.strip()
        path = rest.strip().replace("\\", "/")
        if state not in {"pack", "unpack"}:
            raise RuntimeError(f"unexpected asar list --is-pack state: {raw_line!r}")
        all_paths.add(path)
        if state == "unpack":
            unpacked.add(path)
    return all_paths, unpacked


def verify_unpacked_identical(original_text: str, repacked_text: str) -> None:
    """The repacked archive must keep exactly the official unpacked set.

    A path that is no longer unpacked cannot be loaded (native modules and
    spawned helpers must be real files). A path that became unpacked is also an
    error: it lands on disk as a new file or directory the official layout
    never had, and nested package trees can push it past Windows' path limit.
    """
    _, original_unpacked = parse_asar_listing(original_text)
    _, repacked_unpacked = parse_asar_listing(repacked_text)
    for path in sorted(original_unpacked):
        if path not in repacked_unpacked:
            raise RuntimeError(
                f"repacked app.asar no longer keeps {path} unpacked; the unpack "
                "pattern does not cover the official layout"
            )
    for path in sorted(repacked_unpacked):
        if path not in original_unpacked:
            raise RuntimeError(
                f"repacked app.asar unpacks {path}, which the official archive keeps "
                "packed; the unpack pattern is broader than the official layout"
            )


def windows_desktop_profile_prelude() -> str:
    """Environment inserted before the userData rewrite on Windows: none.

    The macOS copy pins its Computer Use helper's socket and stops the official
    refresh from rewriting a helper it does not own. Windows has neither: in the
    Store build SKY_CUA_SERVICE_NATIVE_PIPE_PATH and
    CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH are only read on the
    `darwin` platform, and the Windows client of @oai/sky starts its helper as a
    child process over stdio, with no pipe or service to share. Setting them
    would do nothing while implying a protection that does not exist.
    """
    return ""


def go_architecture(machine: object) -> str:
    """Go's GOARCH for the machine type exe-info reported for the host."""
    architecture = GO_ARCHITECTURES.get(str(machine))
    if architecture is None:
        raise RuntimeError(
            f"the official app's host executable is built for {machine!r}; "
            "this patcher builds the multiplexer and launcher for "
            + " and ".join(sorted(GO_ARCHITECTURES))
            + " only"
        )
    return architecture


@dataclasses.dataclass(frozen=True)
class SourceIdentity:
    product_version: str
    file_version: str
    product_name: str
    signed: bool
    asar_sha256: str


def source_identity(exe_info: dict, asar_sha256: str) -> SourceIdentity:
    version_info = exe_info.get("versionInfo")
    if not isinstance(version_info, dict):
        version_info = {}
    strings = version_info.get("strings")
    if not isinstance(strings, dict):
        strings = {}
    return SourceIdentity(
        product_version=str(version_info.get("productVersion") or "unknown"),
        file_version=str(version_info.get("fileVersion") or "unknown"),
        product_name=str(strings.get("ProductName") or "unknown"),
        signed=bool(exe_info.get("signed", False)),
        asar_sha256=asar_sha256,
    )


# Exit status of --check-source and of a full run when the only problem is that the
# official build is not one the project has recorded. install.ps1 turns it into a
# question for the person; any other failure is 1.
UNTESTED_SOURCE_EXIT_CODE = 3


class UntestedSourceError(RuntimeError):
    """The official build is not recorded as tested (nothing else is wrong with it yet)."""


def approve_source(identity: SourceIdentity, allow_untested: bool) -> None:
    recorded = TESTED_WINDOWS_SOURCE_BUILDS.get(
        (identity.product_version, identity.file_version)
    )
    allowed = (recorded,) if isinstance(recorded, str) else tuple(recorded or ())
    if identity.asar_sha256 in allowed:
        return
    if not allow_untested:
        raise UntestedSourceError(
            "the source version, build, or app.asar hash is not approved; "
            "review the upstream change or pass --allow-untested-source"
        )
    print(
        "Warning: continuing with an untested official ChatGPT build; "
        "the patch will continue only while every expected anchor matches.",
        file=sys.stderr,
    )


def unreachable_registration_spans(bundle: str) -> list[tuple[int, int]]:
    return [
        match.span()
        for match in WINDOWS_UNREACHABLE_REGISTRATION_PATTERN.finditer(bundle)
    ]


def unreachable_registration_residue(bundle: str) -> re.Match[str] | None:
    """First setAsDefaultProtocolClient mention that is neither retargeted nor
    inside a win32-guarded early-return shape (see the pattern's comment)."""
    guarded = unreachable_registration_spans(bundle)
    for match in PROTOCOL_REGISTRATION_RESIDUE_PATTERN.finditer(bundle):
        if not any(start <= match.start() < end for start, end in guarded):
            return match
    return None


def count_unreachable_registrations(extracted: Path) -> int:
    """How many win32-guarded registrations the main-process bundles carry."""
    return sum(
        len(unreachable_registration_spans(path.read_text(encoding="utf-8")))
        for path in (extracted / ".vite" / "build").glob("*.js")
    )


# The notification-area identities the app registers per build flavour: the GUID
# passed to `new Tray(icon, guid)` on packaged Windows builds. Windows binds a tray
# GUID to the executable that first registered it, so a copy that reuses the
# official app's GUID from another path gets no tray icon at all: closing its window
# then hides it with nothing left to quit it from. Each literal appears exactly once
# in the main bundle (checked on build 12947).
KNOWN_TRAY_GUIDS = (
    "c4c933ff-16f4-47c7-a231-29e6bb84dccc",  # nightly
    "c4607cac-3138-48bd-8b2a-5bc99d08e393",  # internal alpha
    "758400cf-36c7-43be-9335-6354d603be3a",  # public beta
    "e5768d8b-6936-4f45-b1ad-4c5fb414cb35",  # production
)
TRAY_GUID_NAMESPACE = "https://github.com/Mvnshi/codex-subscription-router/tray-icon/"


def router_tray_guid(official_guid: str) -> str:
    """A stable GUID of the copy's own for one of the official tray GUIDs."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, TRAY_GUID_NAMESPACE + official_guid))


def retarget_tray_identity(extracted: Path) -> int:
    """Give the copy its own tray-icon identity; returns how many GUIDs it replaced.

    Zero is not an error (a future build may name the identity differently and the
    only cost is a missing tray icon), but a GUID that appears more than once in a
    bundle is, because replacing it could not be shown to be exact. Bundles are
    rewritten byte for byte apart from the GUID, newlines included.
    """
    replaced = 0
    for bundle_path in sorted((extracted / ".vite" / "build").glob("*.js")):
        with open(bundle_path, encoding="utf-8", newline="") as handle:
            bundle = handle.read()
        changed = False
        for guid in KNOWN_TRAY_GUIDS:
            count = bundle.count(guid)
            if count > 1:
                raise RuntimeError(
                    f"{bundle_path.name} contains the tray GUID {guid} {count} times; "
                    "expected exactly one - re-derive the anchor"
                )
            if count == 1:
                bundle = bundle.replace(guid, router_tray_guid(guid))
                replaced += 1
                changed = True
        if changed:
            with open(bundle_path, "w", encoding="utf-8", newline="") as handle:
                handle.write(bundle)
    return replaced


def retarget_protocol_scheme(extracted: Path) -> int:
    """Register codex-subscription-router:// instead of taking codex:// over.

    Windows protocol handlers are registry entries written at run time by
    app.setAsDefaultProtocolClient; leaving the scheme alone would make the
    copy steal codex:// from the official app on every launch.
    """
    replacements = 0
    for bundle_path in sorted((extracted / ".vite" / "build").glob("*.js")):
        bundle = bundle_path.read_text(encoding="utf-8")
        bundle, count = PROTOCOL_CALL_PATTERN.subn(
            lambda match: f"{match.group(1)}({match.group(2)}{PROTOCOL_SCHEME}{match.group(2)}",
            bundle,
        )
        # Fail closed before writing: a registration whose scheme is not the
        # literal 'codex' (a variable, another literal, an aliased call) was
        # not retargeted, and on Windows setAsDefaultProtocolClient writes
        # HKCU\Software\Classes\<scheme> to point at the copy without asking,
        # so the copy would silently take that scheme over at first launch.
        # A call inside the exact win32-guarded shape above never runs here.
        if unreachable_registration_residue(bundle) is not None:
            raise RuntimeError(
                f"{bundle_path.name} calls setAsDefaultProtocolClient with a scheme "
                f"that is not the literal 'codex'; it was not retargeted and the copy "
                "would register that scheme for itself - re-derive the anchor"
            )
        if count:
            bundle_path.write_text(bundle, encoding="utf-8")
            replacements += count
    return replacements


def longest_path_length(root: Path) -> int:
    longest = len(str(root))
    for entry in root.rglob("*"):
        longest = max(longest, len(str(entry)))
    return longest


def projected_longest_path(source: Path, destination: Path, longest: int) -> int:
    return longest + len(str(destination)) - len(str(source)) + PATH_LENGTH_MARGIN


def require_long_path_support(
    source: Path, destination: Path, longest: int, enabled: bool | None
) -> None:
    projected = projected_longest_path(source, destination, longest)
    if projected < MAX_PATH or enabled is True:
        return
    state = "is not enabled" if enabled is False else "could not be read"
    raise RuntimeError(
        f"the copied app would contain paths of {projected} characters (limit "
        f"{MAX_PATH}) and Windows long-path support {state}. Enable it by setting "
        f"the DWORD {LONG_PATHS_REGISTRY_KEY} to 1 (an elevated PowerShell: "
        "New-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem' "
        "-Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force), sign out and "
        "back in, then rerun"
    )


def staging_shape(destination: Path) -> Path:
    """The longest path the install writes: the staging copy, not the destination.

    tempfile.TemporaryDirectory places the copy under
    destination.parent/.csr-XXXXXXXX/app before the final rename, so the
    path-length check must use that location rather than the destination.
    """
    return (
        destination.parent
        / (STAGING_PREFIX + "x" * STAGING_RANDOM_LENGTH)
        / STAGING_DIRECTORY_NAME
    )


def file_sha256(path: Path) -> str:
    """Hash in chunks: the official app.asar is over half a gigabyte."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def node_executable() -> str:
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("required tool not found: node")
    return node


def asar_command() -> list[str]:
    """Invoke the pinned asar through node directly, never the .cmd shim.

    The npm shim differs per shell and platform; the ESM entry point is the
    same file everywhere and is what the shim would run anyway.
    """
    shared.ensure_asar_tool()
    if not ASAR_CLI.is_file():
        raise RuntimeError("run `npm ci --ignore-scripts` before patching")
    return [node_executable(), str(ASAR_CLI)]


def asar_output(command: list[str]) -> str:
    """Capture asar's listing as UTF-8, whatever the console code page is.

    asar prints archive paths to the pipe as UTF-8; shared.output leaves the
    encoding to Python's text mode, which on Windows is the ANSI code page
    (cp1252, cp932, ...) and either garbles a non-ASCII file name or raises
    UnicodeDecodeError for bytes that page leaves undefined. The shared helper
    itself stays untouched so the macOS patcher is byte-identical.
    """
    return subprocess.check_output(command, text=True, encoding="utf-8").strip()


def run_helper(
    command: list[str], tool: str, *, errors: str = "strict"
) -> subprocess.CompletedProcess:
    """Run a helper and surface its own diagnostic when it fails.

    subprocess.run(check=True) raises CalledProcessError whose message carries
    only the exit status, while scripts/win/pe.mjs's runCli and PowerShell
    write the reason ("not a PE executable", "verification failed: ...") to
    stderr, which capture_output would otherwise discard; docs/WINDOWS.md
    promises that a stopped run names the check that failed. Still fail
    closed: every non-zero exit raises.

    Output is decoded as UTF-8, which is what the node helpers write to a
    pipe whatever the console code page is. errors is the codec error handler:
    "strict" by default, so a helper whose JSON is not UTF-8 is an error rather
    than silently mangled input; a caller whose child does not write UTF-8
    passes "replace" and says why at the call site. A strict-decode failure is
    reported as a RuntimeError on every platform, because subprocess surfaces
    it differently: POSIX raises UnicodeDecodeError from run(), Windows returns
    None for the stream whose reader thread died.
    """
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors=errors,
        )
    except UnicodeDecodeError as error:
        # POSIX: communicate() decodes after reading and raises here.
        raise RuntimeError(f"{tool} wrote output that is not UTF-8: {error}") from error
    if result.stdout is None or result.stderr is None:
        # Windows: communicate() decodes in reader threads; a decode failure
        # kills the thread and the stream comes back as None instead of an
        # exception. Surface the same clear error rather than an AttributeError
        # or a json.loads(None) traceback further down.
        raise RuntimeError(f"{tool} wrote output that is not UTF-8")
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise RuntimeError(f"{tool} failed (exit {result.returncode}): {detail}")
    return result


def helper_json(result: subprocess.CompletedProcess, tool: str) -> object:
    try:
        return json.loads(result.stdout)
    except ValueError as error:
        raise RuntimeError(f"{tool} did not print JSON: {error}") from error


def exe_info(exe: Path) -> dict:
    if not EXE_INFO_SCRIPT.is_file():
        raise RuntimeError(f"missing helper: {EXE_INFO_SCRIPT}")
    result = run_helper([node_executable(), str(EXE_INFO_SCRIPT), str(exe)], "exe-info")
    info = helper_json(result, "exe-info")
    if not isinstance(info, dict):
        raise RuntimeError(f"exe-info did not return an object for {exe}")
    return info


def normalise_integrity_file(value: str) -> str:
    return value.replace("\\", "/").lower()


def integrity_entry_for(exe_info: dict, file: str) -> dict | None:
    entries = exe_info.get("asarIntegrity")
    if not isinstance(entries, list):
        return None
    wanted = normalise_integrity_file(file)
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and normalise_integrity_file(str(entry.get("file", ""))) == wanted
    ]
    if len(matches) > 1:
        raise RuntimeError(f"{file} matches {len(matches)} asar integrity entries")
    return matches[0] if matches else None


def launcher_ldflags(electron_executable_name: str) -> str:
    # go build splits -ldflags on whitespace and honours quotes, so a name
    # containing either could not be carried through -X intact.
    if not electron_executable_name or re.search(r"[\s\"'`]", electron_executable_name):
        raise RuntimeError(
            "the Electron executable name cannot be embedded in the launcher: "
            f"{electron_executable_name!r}"
        )
    return f"-s -w -H=windowsgui -X main.electronExecutable={electron_executable_name}"


def powershell_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def powershell_stopped_processes_command(destination: Path) -> list[str]:
    """List PIDs of processes whose executable lives under destination."""
    prefix = powershell_literal(str(destination) + "\\")
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and "
        f"$_.ExecutablePath.StartsWith({prefix}, "
        "[System.StringComparison]::OrdinalIgnoreCase) } | "
        "ForEach-Object { $_.ProcessId }"
    )
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]


def icacls_commands(state_root: Path, username: str) -> list[list[str]]:
    """Equivalent of chmod 0700: only the user and SYSTEM can reach the state.

    Two invocations, in order. /inheritance:r drops only inherited ACEs and
    /grant:r replaces only the named SIDs' explicit ACEs, so an explicit ACE
    another principal already held on an existing state root (granted earlier
    by hand or by a sync tool) would survive them and keep the control token
    readable. /reset first discards every explicit ACE on the root; it is a
    separate icacls syntax form, hence its own command. Children pick up the
    new inheritable ACEs through normal propagation; explicit ACEs on children
    themselves are out of scope (a /T reset would recurse every account home).
    """
    return [
        ["icacls", str(state_root), "/reset"],
        [
            "icacls",
            str(state_root),
            "/inheritance:r",
            "/grant:r",
            f"{username}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F",
        ],
    ]


def shortcut_command(
    launcher: Path,
    shortcut_path: Path,
    *,
    icon_path: Path | None = None,
    desktop: bool = False,
    legacy_path: Path | None = None,
) -> list[str]:
    """One PowerShell command that creates the shortcuts and tidies the old one.

    The Desktop folder is asked of Windows from inside the script because it can
    be redirected (OneDrive), which the environment does not say. A legacy
    shortcut is only removed when it points at this launcher.
    """
    launcher_literal = powershell_literal(launcher)
    icon_statement = (
        f" $shortcut.IconLocation = {powershell_literal(str(icon_path) + ',0')};" if icon_path else ""
    )
    lines = [
        "$shell = New-Object -ComObject WScript.Shell",
        "function Save-Shortcut([string]$path) {"
        " $shortcut = $shell.CreateShortcut($path);"
        f" $shortcut.TargetPath = {launcher_literal};"
        f" $shortcut.WorkingDirectory = {powershell_literal(launcher.parent)};"
        f" $shortcut.Description = {powershell_literal(SHORTCUT_DESCRIPTION)};"
        + icon_statement
        + " $shortcut.Save() }",
        f"Save-Shortcut {powershell_literal(shortcut_path)}",
    ]
    if desktop:
        # No Desktop folder (a redirected or removed one) means no Desktop shortcut, not an error.
        lines += [
            "$desktopFolder = [Environment]::GetFolderPath('Desktop')",
            "if ($desktopFolder) { "
            f"$desktopPath = Join-Path $desktopFolder {powershell_literal(f'{SHORTCUT_DISPLAY_NAME}.lnk')}; "
            'Save-Shortcut $desktopPath; Write-Host "Desktop shortcut: $desktopPath" }',
        ]
    if legacy_path is not None:
        legacy_literal = powershell_literal(legacy_path)
        lines.append(
            f"if (Test-Path -LiteralPath {legacy_literal}) {{"
            f" if ($shell.CreateShortcut({legacy_literal}).TargetPath -eq {launcher_literal}) {{"
            f" Remove-Item -LiteralPath {legacy_literal}; Write-Host 'Removed the old shortcut name.' }} }}"
        )
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", "; ".join(lines)]


# --- Windows-only I/O -------------------------------------------------------


def current_username(env: Mapping[str, str]) -> str:
    username = environment_value(env, "USERNAME")
    if username is None:
        username = os.getlogin()
    domain = environment_value(env, "USERDOMAIN")
    return f"{domain}\\{username}" if domain else username


def harden_state_root(state_root: Path) -> None:
    # Fail closed: a state root readable by other local users would expose
    # the control token and every isolated account's credentials.
    for command in icacls_commands(state_root, current_username(os.environ)):
        shared.run(command)


def ensure_destination_processes_stopped(destination: Path) -> None:
    # errors="replace": Windows PowerShell 5.1 writes redirected output in the
    # console's OEM code page (cp850, cp437, ...), not UTF-8, so a localized
    # failure text ("Accès refusé") is not valid UTF-8 and a strict decode
    # would raise UnicodeDecodeError, a ValueError that main() does not turn
    # into its "patch failed: ..." line. The PIDs on stdout are ASCII in every
    # code page, so replacement can only touch the diagnostic, which still
    # names the failed check legibly.
    result = run_helper(
        powershell_stopped_processes_command(destination),
        "process check (Get-CimInstance)",
        errors="replace",
    )
    pids = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if pids:
        raise RuntimeError(
            "quit the running app before replacing it; processes still running "
            f"from {destination}: {', '.join(pids)}"
        )


def long_paths_enabled() -> bool | None:
    try:
        import winreg  # noqa: PLC0415  (Windows-only module)
    except ImportError:
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            value, kind = winreg.QueryValueEx(key, "LongPathsEnabled")
    except OSError:
        return None
    if kind != winreg.REG_DWORD:
        return None
    return value == 1


def start_menu_programs_directory(env: Mapping[str, str]) -> Path:
    app_data = environment_value(env, "APPDATA")
    if app_data is None:
        raise RuntimeError("APPDATA is not set")
    return Path(app_data) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def start_menu_shortcut_path(env: Mapping[str, str]) -> Path:
    return start_menu_programs_directory(env) / START_MENU_SHORTCUT_NAME


def install_icons(stage: Path) -> list[str]:
    """Give the staged copy the router's icons; returns what it replaced.

    Only files the official app already ships are replaced (the app looks them
    up by name). A router icon missing from the repository is an error rather
    than a silent skip, since the repository is supposed to carry all three.
    """
    resources = stage / "resources"
    replaced: list[str] = []
    for source_name, targets in ICON_REPLACEMENTS:
        source = ICON_DIRECTORY / source_name
        if not source.is_file():
            raise RuntimeError(f"missing router icon {source}")
        for target_name in targets:
            target = resources / target_name
            if target.is_file():
                shutil.copyfile(source, target)
                replaced.append(target_name)
    shutil.copyfile(ICON_DIRECTORY / "codex-router.ico", stage / SHORTCUT_ICON_NAME)
    return replaced


def rewrite_asar_integrity(executable: Path, entry: dict, digest: str) -> str:
    """Record the repacked archive's header digest; returns the stored value."""
    if str(entry.get("alg", "")).upper() != "SHA256":
        raise RuntimeError(
            f"asar integrity entry {entry.get('file')!r} uses algorithm "
            f"{entry.get('alg')!r}, not SHA256"
        )
    if not SET_ASAR_INTEGRITY_SCRIPT.is_file():
        raise RuntimeError(f"missing helper: {SET_ASAR_INTEGRITY_SCRIPT}")
    result = run_helper(
        [
            node_executable(),
            str(SET_ASAR_INTEGRITY_SCRIPT),
            str(executable),
            str(entry["file"]),
            digest,
        ],
        "set-asar-integrity",
    )
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    updated = integrity_entry_for(
        {"asarIntegrity": helper_json(result, "set-asar-integrity")}, entry["file"]
    )
    if updated is None or str(updated.get("value", "")).lower() != digest.lower():
        raise RuntimeError("set-asar-integrity did not record the new asar digest")
    return str(updated["value"])


def move_directory(source: Path, target: Path) -> None:
    """Move a directory tree, atomically when the volume allows it.

    Path.rename is one MoveFileEx call and atomic within a volume, so it is
    tried first. The backup lives under the state root
    (%USERPROFILE%\\.codex-mux) while the destination defaults to
    %LOCALAPPDATA%\\Programs, and either can sit on another volume (a
    --destination on D:, a relocated profile or LOCALAPPDATA), where the
    rename fails with ERROR_NOT_SAME_DEVICE (errno EXDEV). Only that failure
    falls back to shutil.move, which copies the tree and then removes the
    original: not atomic, but it never leaves the tree missing at both paths.
    Every other OSError propagates unchanged: shutil.move on its own would
    also copy over a transient lock or a permission problem and could then
    fail half-way through removing the original, leaving two partial trees.
    """
    try:
        source.rename(target)
    except OSError as error:
        if (
            error.errno != errno.EXDEV
            and getattr(error, "winerror", None) != ERROR_NOT_SAME_DEVICE
        ):
            raise
        shutil.move(source, target)


def swap_into_place(
    stage: Path, destination: Path, backup_directory: Path, had_app: bool
) -> None:
    """Move an existing install to its backup, then rename the staged copy in.

    The macOS flow renames the app and then its Computer Use helper, so its
    handler has to park a half-installed new copy under failed-install before
    restoring. The Windows flow performs exactly one atomic rename after the
    backup move, so when something raises either the backup move itself
    failed and the previous install is still untouched at the destination
    (Defender scanning the just-closed app can hold a transient lock), or the
    backup move succeeded and the single stage rename failed, leaving nothing
    at the destination. There is never a half-installed new copy to park, so
    the handler is a pure restore of the backup and the staged copy is
    discarded with the temporary directory. The failed-install move once
    copied from macOS misfired here: with destination.exists() true after a
    failed backup move it moved the user's working install into
    failed-install and restored nothing.
    """
    app_backup = backup_directory / destination.name
    try:
        if had_app:
            move_directory(destination, app_backup)
            print(f"Existing copy moved to {app_backup}")
        # The stage is in destination.parent, hence on the destination's
        # volume; a plain rename is atomic there and deliberately has no copy
        # fallback, which would be exactly the half-installed copy avoided.
        stage.rename(destination)
    except OSError:
        if had_app and not destination.exists() and app_backup.exists():
            # move_directory, not rename: a backup that went to another volume
            # has to come back the same way.
            move_directory(app_backup, destination)
        raise


def patch_app(
    source: Path | None,
    destination: Path | None,
    force: bool,
    allow_untested_source: bool,
    electron_executable: str | None,
    codex_executable: str | None,
    create_shortcut: bool,
    create_desktop_shortcut: bool = True,
    check_source_only: bool = False,
) -> None:
    if sys.platform != "win32":
        raise RuntimeError(
            "scripts/patch_app_windows.py runs on Windows; the tests run anywhere"
        )
    env = os.environ
    source = discover_source(
        env,
        source.expanduser() if source else None,
        store_install_candidates() if source is None else (),
    ).resolve()
    if is_store_install(source):
        print(
            "Source is a Microsoft Store (MSIX) install; it is copied read-only and "
            "the official package stays untouched."
        )
    destination = (
        destination.expanduser() if destination else default_destination(env)
    ).resolve()
    if source == destination:
        raise RuntimeError(
            "source and destination must be different; "
            "the original app is never patched in place"
        )
    if destination.is_relative_to(source) or source.is_relative_to(destination):
        raise RuntimeError("source and destination must not contain one another")

    electron_exe = select_electron_executable(source, electron_executable)
    print(f"Source install: {source}")
    print(f"Electron executable: {electron_exe.name}")
    # Everything that depends only on the source is checked here, before the
    # tool probes, the copy of a several-hundred-MB tree and the two Go
    # builds, so a layout this patcher does not understand stops the run in
    # seconds. The stage is a verbatim copy, so these results hold there too.
    ldflags = launcher_ldflags(electron_exe.name)
    source_codex = locate_codex_executable(source, codex_executable)
    if source_codex.with_name(REAL_CODEX_EXECUTABLE_NAME).exists():
        raise RuntimeError(f"source app already contains {REAL_CODEX_EXECUTABLE_NAME}")
    for tool in ("go", "node", "npm"):
        shared.require_tool(tool)
    asar = asar_command()
    source_asar = source / "resources" / "app.asar"
    # The official archive's own packed/unpacked flags decide how it is repacked;
    # the stage is a verbatim copy, so the source's listing holds for it too.
    original_listing = asar_output([*asar, "list", "--is-pack", str(source_asar)])
    patterns = unpack_patterns(original_listing)
    info = exe_info(electron_exe)
    goarch = go_architecture(info.get("machine"))
    source_asar_hash = file_sha256(source_asar)
    identity = source_identity(info, source_asar_hash)
    print(
        f"Source {identity.product_name} version: {identity.product_version} "
        f"(file {identity.file_version}), {info.get('machine', 'unknown')}, "
        f"{'signed' if identity.signed else 'unsigned'}, app.asar {source_asar_hash}"
    )
    approve_source(identity, allow_untested_source)
    if check_source_only:
        print("Source check passed; nothing was copied or built.")
        return

    token = shared.load_or_create_token()
    harden_state_root(shared.DEFAULT_STATE_ROOT)
    if destination.exists() and not force:
        raise RuntimeError(
            f"destination exists: {destination} "
            "(pass --force to create a recoverable backup)"
        )
    if force and destination.exists():
        ensure_destination_processes_stopped(destination)
    require_long_path_support(
        source, staging_shape(destination), longest_path_length(source), long_paths_enabled()
    )
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix=STAGING_PREFIX, dir=destination.parent) as temporary:
        temporary_path = Path(temporary)
        stage = temporary_path / STAGING_DIRECTORY_NAME
        extracted = temporary_path / "asar"
        mux = temporary_path / CODEX_EXECUTABLE_NAME

        print("Copying the official app…")
        shutil.copytree(source, stage, symlinks=False)
        print("Building multiplexer…")
        shared.build_go_program(mux, "./cmd/codex-mux", goos="windows", goarch=goarch)
        print("Building launcher…")
        shared.build_go_program(
            stage / LAUNCHER_EXECUTABLE_NAME,
            "./cmd/codex-router-launcher",
            goos="windows",
            goarch=goarch,
            ldflags=ldflags,
        )

        resources = stage / "resources"
        original_asar = resources / "app.asar"
        print("Patching desktop profile and renderer…")
        shared.run([*asar, "extract", str(original_asar), str(extracted)])
        shared.isolate_desktop_profile(extracted, windows_desktop_profile_prelude())
        shared.install_ui_test_bridge(extracted)
        protocol_replacements = retarget_protocol_scheme(extracted)
        guarded_registrations = count_unreachable_registrations(extracted)
        if protocol_replacements == 0 and guarded_registrations:
            print(
                f"Protocol registration is skipped on Windows by the app itself "
                f"({guarded_registrations} guarded call(s)); nothing to retarget."
            )
        elif protocol_replacements == 0:
            # Only reachable when no setAsDefaultProtocolClient call exists at
            # all (a registration the installer performs, say); a call with a
            # non-literal scheme has already stopped the patch above.
            print(
                "Warning: the main-process bundles contain no protocol-client call to "
                "retarget; if the installer registered codex://, check after launch "
                "that the copy did not take over that handler.",
                file=sys.stderr,
            )
        else:
            print(f"Retargeted {protocol_replacements} protocol-client registration(s)")
        tray_replacements = retarget_tray_identity(extracted)
        if tray_replacements:
            print(f"Gave the copy its own tray-icon identity ({tray_replacements} GUID(s))")
        else:
            print(
                "Warning: no known tray-icon GUID was found, so the copy shares the "
                "official app's tray identity and may show no tray icon; closing its "
                "window would then leave it running with no way to quit it.",
                file=sys.stderr,
            )
        shared.patch_renderer(extracted, token)

        repacked_asar = temporary_path / "app.asar"
        pack_command = [*asar, "pack"]
        if patterns.unpack is not None:
            pack_command.extend(("--unpack", patterns.unpack))
        if patterns.unpack_dir is not None:
            pack_command.extend(("--unpack-dir", patterns.unpack_dir))
        shared.run([*pack_command, str(extracted), str(repacked_asar)])
        repacked_listing = asar_output([*asar, "list", "--is-pack", str(repacked_asar)])
        verify_unpacked_identical(original_listing, repacked_listing)
        shutil.copy2(repacked_asar, original_asar)
        repacked_unpacked = temporary_path / "app.asar.unpacked"
        if patterns == UnpackPatterns(None, None):
            if repacked_unpacked.exists():
                raise RuntimeError("ASAR pack produced an unpacked tree without a pattern")
        else:
            if not repacked_unpacked.is_dir():
                raise RuntimeError("ASAR pack did not produce its unpacked native tree")
            shutil.copytree(
                repacked_unpacked, resources / "app.asar.unpacked", dirs_exist_ok=True
            )

        real_codex = stage / source_codex.relative_to(source)
        parked_codex = real_codex.with_name(REAL_CODEX_EXECUTABLE_NAME)
        # Already refused on the source; repeated on the stage as belt and
        # braces since the rename below would otherwise overwrite it.
        if parked_codex.exists():
            raise RuntimeError(f"source app already contains {REAL_CODEX_EXECUTABLE_NAME}")
        real_codex.rename(parked_codex)
        shutil.copy2(mux, real_codex)
        print(f"Multiplexer installed at {real_codex.relative_to(stage)}")

        replaced_icons = install_icons(stage)
        if replaced_icons:
            print(f"Gave the copy its own icons ({', '.join(replaced_icons)})")
        else:
            print(
                "Warning: none of the app's icon files were found, so the copy keeps "
                "the official app's icons.",
                file=sys.stderr,
            )

        # Not signed: there is no codesign equivalent here, and rewriting the
        # integrity resource strips the official Authenticode signature.
        staged_electron_exe = stage / electron_exe.name
        entries = info.get("asarIntegrity")
        if entries is None:
            print(
                "The source executable carries no embedded asar integrity resource "
                "(integrity fuse presumably disabled); nothing to rewrite."
            )
        else:
            entry = integrity_entry_for(info, INTEGRITY_ASAR_FILE)
            if entry is None:
                raise RuntimeError(
                    f"no asar integrity entry matches {INTEGRITY_ASAR_FILE}; entries: "
                    + ", ".join(str(item.get("file")) for item in entries if isinstance(item, dict))
                )
            digest = shared.asar_header_digest(original_asar)
            recorded = rewrite_asar_integrity(staged_electron_exe, entry, digest)
            print(f"Recorded asar header digest {recorded}")

        backup_suffix = time.strftime("%Y%m%d-%H%M%S")
        backup_directory = shared.DEFAULT_STATE_ROOT / "backups" / backup_suffix
        had_app = destination.exists()
        if had_app:
            backup_directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            backup_directory.parent.chmod(0o700)
            backup_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        swap_into_place(stage, destination, backup_directory, had_app)

    launcher = destination / LAUNCHER_EXECUTABLE_NAME
    if create_shortcut:
        # The install is complete and launchable at this point; a shortcut
        # problem must not report it as failed.
        try:
            shortcut = start_menu_shortcut_path(env)
            shortcut.parent.mkdir(parents=True, exist_ok=True)
            shared.run(
                shortcut_command(
                    launcher,
                    shortcut,
                    icon_path=destination / SHORTCUT_ICON_NAME,
                    desktop=create_desktop_shortcut,
                    legacy_path=shortcut.parent / LEGACY_SHORTCUT_NAME,
                )
            )
            print(f"Start menu shortcut: {shortcut}")
        except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
            print(f"Warning: could not create the shortcuts: {error}", file=sys.stderr)

    print(destination)
    print(launcher)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        patch_app(
            args.source,
            args.destination,
            args.force,
            args.allow_untested_source,
            args.electron_executable,
            args.codex_executable,
            not args.no_shortcut,
            not args.no_desktop_shortcut,
            args.check_source,
        )
    except UntestedSourceError as error:
        # A question for the person, not a failure, when only checking.
        print(error if args.check_source else f"patch failed: {error}", file=sys.stderr)
        return UNTESTED_SOURCE_EXIT_CODE
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"patch failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
