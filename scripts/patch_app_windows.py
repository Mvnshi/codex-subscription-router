#!/usr/bin/env python3
"""Create an independent, profile-isolated copy of the official ChatGPT desktop app for Windows with Codex multiplexing.

PROVISIONAL. No official Windows build of the ChatGPT/Codex desktop app has
been exercised with this patcher yet: TESTED_WINDOWS_SOURCE_BUILDS is empty,
so every run needs --allow-untested-source until a build has been verified.
Every assumption about the Windows layout (where the install lives, which
executable is the Electron host, where the bundled codex.exe sits, which
node_modules stay unpacked, whether the executable carries an embedded asar
integrity resource) is checked at run time with exact, fail-closed checks
rather than assumed, and the checks and their outcomes are recorded in
docs/WINDOWS.md.

Differences from the macOS patcher (scripts/patch_app.py), on purpose:
- Nothing is code-signed. Windows has no codesign step; rewriting the
  INTEGRITY/ELECTRONASAR resource of the copied Electron executable drops
  its Authenticode signature, so the copy runs unsigned (SmartScreen may
  warn once).
- Computer Use identity is not patched and the managed Computer Use service
  is not pinned: the Swift helper is macOS-only and no Windows helper has
  been verified. The copy is pointed at a named pipe the official app never
  uses so the two builds cannot share a helper by accident.
- The launcher is a Go program (cmd/codex-router-launcher) built as
  "Codex Subscription Router.exe" beside the Electron executable.
- The URL scheme is retargeted in the main-process bundles (Windows registers
  protocol handlers at run time from JavaScript, not from an Info.plist).
"""

from __future__ import annotations

import argparse
import dataclasses
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
from collections.abc import Mapping
from pathlib import Path

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
START_MENU_SHORTCUT_NAME = f"{DESTINATION_DIRECTORY_NAME}.lnk"
COMPUTER_USE_PIPE_PATH = r"\\.\pipe\codex-subscription-router-computer-use"
LONG_PATHS_REGISTRY_KEY = (
    "HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled"
)
# Windows MAX_PATH; paths at or beyond it need LongPathsEnabled=1 (and a
# long-path-aware Python, which python.org builds are).
MAX_PATH = 260
# Slack for the renames the patcher performs on the copied tree
# (codex.exe -> codex.real.exe adds five characters) and for the trailing NUL.
PATH_LENGTH_MARGIN = 8
# tempfile.mkdtemp appends eight random characters to the prefix.
STAGING_PREFIX = ".codex-subscription-router-"
STAGING_RANDOM_LENGTH = 8
EXE_INFO_SCRIPT = shared.PROJECT_ROOT / "scripts" / "win" / "exe-info.mjs"
SET_ASAR_INTEGRITY_SCRIPT = (
    shared.PROJECT_ROOT / "scripts" / "win" / "set-asar-integrity.mjs"
)
ASAR_CLI = (
    shared.PROJECT_ROOT / "node_modules" / "@electron" / "asar" / "bin" / "asar.mjs"
)

# Keyed by (versionInfo.productVersion, versionInfo.fileVersion) of the
# Electron executable -> sha256 of the whole app.asar, mirroring
# patch_app.TESTED_SOURCE_BUILDS. Deliberately empty: no Windows build has
# been exercised, so nothing here is verified, and --allow-untested-source is
# required until the first entry lands together with its anchor review.
TESTED_WINDOWS_SOURCE_BUILDS: dict[tuple[str, str], str] = {}

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
        help="Do not create the Start menu shortcut.",
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


def refuse_store_install(path: Path) -> None:
    # MSIX/Store packages under WindowsApps are ACL-locked and their
    # executables only start inside the package identity; a copy would neither
    # read completely nor launch.
    if any(part.lower() == "windowsapps" for part in path.parts):
        raise RuntimeError(
            f"{path} is a Microsoft Store/MSIX install; Store/MSIX installs cannot be "
            "copied. Install the ChatGPT desktop app from the downloadable installer "
            "and pass --source if it is not discovered"
        )


def discover_source(env: Mapping[str, str], explicit: Path | None) -> Path:
    if explicit is not None:
        refuse_store_install(explicit)
        if not is_electron_app_directory(explicit):
            raise RuntimeError(
                f"not an Electron app directory (no resources\\app.asar): {explicit}"
            )
        return explicit
    examined = candidate_source_directories(env)
    qualifying = [candidate for candidate in examined if is_electron_app_directory(candidate)]
    if len(qualifying) != 1:
        examined_text = ", ".join(str(candidate) for candidate in examined) or "(none)"
        raise RuntimeError(
            f"expected exactly one official install, found {len(qualifying)} "
            f"(examined: {examined_text}); pass --source"
        )
    refuse_store_install(qualifying[0])
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
        candidate = app_dir / override
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
    if len(matches) != 1:
        found = ", ".join(str(entry.relative_to(app_dir)) for entry in matches) or "(none)"
        raise RuntimeError(
            f"expected one bundled {CODEX_EXECUTABLE_NAME} under {app_dir}, found "
            f"{len(matches)} ({found}); pass --codex-executable RELPATH"
        )
    return matches[0]


def unpack_globs(app_dir: Path) -> str | None:
    """Derive the --unpack-dir pattern from the official app.asar.unpacked tree.

    The macOS patcher hard-codes its native module list; the Windows layout is
    unknown, so the packages the official build left unpacked are read from
    disk. Only node_modules packages are understood; anything else fails
    closed rather than being repacked into the archive, where native .node
    files and spawned helpers cannot be loaded from.
    """
    unpacked = app_dir / "resources" / "app.asar.unpacked"
    if not unpacked.is_dir():
        return None
    others = sorted(entry.name for entry in unpacked.iterdir() if entry.name != "node_modules")
    if others:
        raise RuntimeError(
            "app.asar.unpacked contains entries other than node_modules, which this "
            f"patcher does not know how to keep unpacked: {', '.join(others)}"
        )
    node_modules = unpacked / "node_modules"
    if not node_modules.is_dir():
        raise RuntimeError("app.asar.unpacked has no node_modules directory")
    names = sorted(entry.name for entry in node_modules.iterdir() if entry.is_dir())
    if not names:
        raise RuntimeError("app.asar.unpacked/node_modules contains no packages")
    if len(names) == 1:
        # minimatch does not expand a single-alternative brace group.
        return f"node_modules/{names[0]}"
    return "node_modules/{" + ",".join(names) + "}"


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


def verify_unpacked_preserved(original_text: str, repacked_text: str) -> None:
    """Every path the official archive kept unpacked must stay unpacked."""
    _, original_unpacked = parse_asar_listing(original_text)
    _, repacked_unpacked = parse_asar_listing(repacked_text)
    for path in sorted(original_unpacked):
        if path not in repacked_unpacked:
            raise RuntimeError(
                f"repacked app.asar no longer keeps {path} unpacked; the unpack "
                "pattern does not cover the official layout"
            )


def windows_desktop_profile_prelude() -> str:
    """Environment inserted before the userData rewrite on Windows.

    There is no verified Computer Use helper on Windows, so instead of
    sharing the official app's pipe the copy is pointed at a named pipe the
    official app never uses; the canonical-refresh skip keeps the copy from
    rewriting a helper it does not own.
    """
    computer_use_pipe = json.dumps(COMPUTER_USE_PIPE_PATH)
    return (
        f"process.env.SKY_CUA_SERVICE_NATIVE_PIPE_PATH={computer_use_pipe};"
        "process.env.CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH=`1`;"
    )


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


def approve_source(identity: SourceIdentity, allow_untested: bool) -> None:
    expected = TESTED_WINDOWS_SOURCE_BUILDS.get(
        (identity.product_version, identity.file_version)
    )
    if expected is not None and expected == identity.asar_sha256:
        return
    if not allow_untested:
        raise RuntimeError(
            "the source version, build, or app.asar hash is not approved; "
            "review the upstream change or pass --allow-untested-source"
        )
    print(
        "Warning: continuing with an untested official ChatGPT build; "
        "the patch will continue only while every expected anchor matches.",
        file=sys.stderr,
    )


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
    destination.parent/.codex-subscription-router-XXXXXXXX/<name> before the
    final rename, so the path-length check must use that deeper location.
    """
    return (
        destination.parent
        / (STAGING_PREFIX + "x" * STAGING_RANDOM_LENGTH)
        / DESTINATION_DIRECTORY_NAME
    )


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


def exe_info(exe: Path) -> dict:
    if not EXE_INFO_SCRIPT.is_file():
        raise RuntimeError(f"missing helper: {EXE_INFO_SCRIPT}")
    result = subprocess.run(
        [node_executable(), str(EXE_INFO_SCRIPT), str(exe)],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    info = json.loads(result.stdout)
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


def icacls_command(state_root: Path, username: str) -> list[str]:
    """Equivalent of chmod 0700: only the user and SYSTEM can reach the state."""
    return [
        "icacls",
        str(state_root),
        "/inheritance:r",
        "/grant:r",
        f"{username}:(OI)(CI)F",
        "*S-1-5-18:(OI)(CI)F",
    ]


def shortcut_command(launcher: Path, shortcut_path: Path) -> list[str]:
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$shortcut = $shell.CreateShortcut({powershell_literal(shortcut_path)}); "
        f"$shortcut.TargetPath = {powershell_literal(launcher)}; "
        f"$shortcut.WorkingDirectory = {powershell_literal(launcher.parent)}; "
        "$shortcut.Save()"
    )
    return ["powershell", "-NoProfile", "-NonInteractive", "-Command", script]


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
    shared.run(icacls_command(state_root, current_username(os.environ)))


def ensure_destination_processes_stopped(destination: Path) -> None:
    result = subprocess.run(
        powershell_stopped_processes_command(destination),
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
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


def start_menu_shortcut_path(env: Mapping[str, str]) -> Path:
    app_data = environment_value(env, "APPDATA")
    if app_data is None:
        raise RuntimeError("APPDATA is not set")
    return (
        Path(app_data)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / START_MENU_SHORTCUT_NAME
    )


def rewrite_asar_integrity(executable: Path, entry: dict, digest: str) -> str:
    """Record the repacked archive's header digest; returns the stored value."""
    if str(entry.get("alg", "")).upper() != "SHA256":
        raise RuntimeError(
            f"asar integrity entry {entry.get('file')!r} uses algorithm "
            f"{entry.get('alg')!r}, not SHA256"
        )
    if not SET_ASAR_INTEGRITY_SCRIPT.is_file():
        raise RuntimeError(f"missing helper: {SET_ASAR_INTEGRITY_SCRIPT}")
    result = subprocess.run(
        [
            node_executable(),
            str(SET_ASAR_INTEGRITY_SCRIPT),
            str(executable),
            str(entry["file"]),
            digest,
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    updated = integrity_entry_for({"asarIntegrity": json.loads(result.stdout)}, entry["file"])
    if updated is None or str(updated.get("value", "")).lower() != digest.lower():
        raise RuntimeError("set-asar-integrity did not record the new asar digest")
    return str(updated["value"])


def patch_app(
    source: Path | None,
    destination: Path | None,
    force: bool,
    allow_untested_source: bool,
    electron_executable: str | None,
    codex_executable: str | None,
    create_shortcut: bool,
) -> None:
    if sys.platform != "win32":
        raise RuntimeError(
            "scripts/patch_app_windows.py runs on Windows; the tests run anywhere"
        )
    env = os.environ
    source = discover_source(env, source.expanduser() if source else None).resolve()
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
    for tool in ("go", "node", "npm"):
        shared.require_tool(tool)
    info = exe_info(electron_exe)
    source_asar = source / "resources" / "app.asar"
    source_asar_hash = hashlib.sha256(source_asar.read_bytes()).hexdigest()
    identity = source_identity(info, source_asar_hash)
    print(
        f"Source {identity.product_name} version: {identity.product_version} "
        f"(file {identity.file_version}), {info.get('machine', 'unknown')}, "
        f"{'signed' if identity.signed else 'unsigned'}, app.asar {source_asar_hash}"
    )
    approve_source(identity, allow_untested_source)

    asar = asar_command()
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
        stage = temporary_path / DESTINATION_DIRECTORY_NAME
        extracted = temporary_path / "asar"
        mux = temporary_path / CODEX_EXECUTABLE_NAME

        print("Copying the official app…")
        shutil.copytree(source, stage, symlinks=False)
        print("Building multiplexer…")
        shared.build_go_program(mux, "./cmd/codex-mux", goos="windows")
        print("Building launcher…")
        shared.build_go_program(
            stage / LAUNCHER_EXECUTABLE_NAME,
            "./cmd/codex-router-launcher",
            goos="windows",
            ldflags=launcher_ldflags(electron_exe.name),
        )

        resources = stage / "resources"
        original_asar = resources / "app.asar"
        print("Patching desktop profile and renderer…")
        original_listing = shared.output([*asar, "list", "--is-pack", str(original_asar)])
        shared.run([*asar, "extract", str(original_asar), str(extracted)])
        shared.isolate_desktop_profile(extracted, windows_desktop_profile_prelude())
        shared.install_ui_test_bridge(extracted)
        protocol_replacements = retarget_protocol_scheme(extracted)
        if protocol_replacements == 0:
            print(
                "Warning: no protocol-client registration was retargeted; check after "
                "launch that the copy did not take over the codex:// handler.",
                file=sys.stderr,
            )
        else:
            print(f"Retargeted {protocol_replacements} protocol-client registration(s)")
        shared.patch_renderer(extracted, token)

        repacked_asar = temporary_path / "app.asar"
        globs = unpack_globs(stage)
        pack_command = [*asar, "pack"]
        if globs is not None:
            pack_command.extend(("--unpack-dir", globs))
        shared.run([*pack_command, str(extracted), str(repacked_asar)])
        repacked_listing = shared.output([*asar, "list", "--is-pack", str(repacked_asar)])
        verify_unpacked_preserved(original_listing, repacked_listing)
        shutil.copy2(repacked_asar, original_asar)
        repacked_unpacked = temporary_path / "app.asar.unpacked"
        if globs is None:
            if repacked_unpacked.exists():
                raise RuntimeError("ASAR pack produced an unpacked tree without a pattern")
        else:
            if not repacked_unpacked.is_dir():
                raise RuntimeError("ASAR pack did not produce its unpacked native tree")
            shutil.copytree(
                repacked_unpacked, resources / "app.asar.unpacked", dirs_exist_ok=True
            )

        real_codex = locate_codex_executable(stage, codex_executable)
        parked_codex = real_codex.with_name(REAL_CODEX_EXECUTABLE_NAME)
        if parked_codex.exists():
            raise RuntimeError(f"source app already contains {REAL_CODEX_EXECUTABLE_NAME}")
        real_codex.rename(parked_codex)
        shutil.copy2(mux, real_codex)
        print(f"Multiplexer installed at {real_codex.relative_to(stage)}")

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
        app_backup = backup_directory / destination.name
        had_app = destination.exists()
        if had_app:
            backup_directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            backup_directory.parent.chmod(0o700)
            backup_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        try:
            if had_app:
                destination.rename(app_backup)
                print(f"Existing copy moved to {app_backup}")
            stage.rename(destination)
        except OSError:
            failed_directory = backup_directory / "failed-install"
            failed_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if destination.exists():
                destination.rename(failed_directory / destination.name)
            if app_backup.exists():
                app_backup.rename(destination)
            raise

    launcher = destination / LAUNCHER_EXECUTABLE_NAME
    if create_shortcut:
        # The install is complete and launchable at this point; a shortcut
        # problem must not report it as failed.
        try:
            shortcut = start_menu_shortcut_path(env)
            shortcut.parent.mkdir(parents=True, exist_ok=True)
            shared.run(shortcut_command(launcher, shortcut))
            print(f"Start menu shortcut: {shortcut}")
        except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
            print(f"Warning: could not create the Start menu shortcut: {error}", file=sys.stderr)

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
        )
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"patch failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
