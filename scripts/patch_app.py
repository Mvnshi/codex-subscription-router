#!/usr/bin/env python3
"""Create an independently signed ChatGPT.app copy with Codex multiplexing."""

from __future__ import annotations

import argparse
import hashlib
import struct
import json
import mmap
import os
import plistlib
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROJECT_VERSION = (PROJECT_ROOT / "VERSION").read_text(encoding="utf-8").strip()
DEFAULT_SOURCE = Path("/Applications/ChatGPT.app")
DEFAULT_DESTINATION = Path.home() / "Applications" / "Codex Subscription Router.app"
DEFAULT_STATE_ROOT = Path.home() / ".codex-mux"
CONTROL_PORT = 48123
DESKTOP_PROFILE_NAME = "Codex Subscription Router"
DESKTOP_BUNDLE_IDENTIFIER = "app.cdxmux.multi"
OPENAI_DESKTOP_CODE_IDENTIFIER = "com.openai.codex"
OPENAI_COMPUTER_USE_BUNDLE_IDENTIFIER = "com.openai.sky.CUAService"
COMPUTER_USE_BUNDLE_IDENTIFIER = "com.cdxmux.sky.CUAService"
COMPUTER_USE_DISPLAY_NAME = "Codex Subscription Router Computer Use"
COMPUTER_USE_APP_NAME = f"{COMPUTER_USE_DISPLAY_NAME}.app"
LAUNCH_SERVICES_REGISTER = Path(
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)
ASAR_UNPACK_DIRECTORIES = (
    "node_modules/{@worklouder,better-sqlite3,node-mac-permissions,node-pty,objc-js}"
)
PREFERRED_SIGNING_IDENTITY_PREFIXES = (
    "Developer ID Application:",
    "Apple Development:",
)
OPENAI_INTERNAL_TEAM_IDENTIFIER = "HX7739G8FX"
OPENAI_DISTRIBUTION_TEAM_IDENTIFIER = "2DC432GLL2"
TESTED_SOURCE_BUILDS = {
    (
        "26.803.61601",
        "6396",
    ): "d5a44ed9e2f1db5f81dbbe85408aed256f3203c5b16f00817bb9d7cd941343cf",
    (
        "26.810.52044",
        "6662",
    ): "6e7e8791b8bf69a586ff994721fff518af391d9efdc66cd2e620dd2a4aedc90f",
    (
        "26.901.22334",
        "7746",
    ): "405f0e1600fc63851abe4c763ec0546f56c32da312c2c2745e2b997c579ce0d0",
    (
        "26.901.51231",
        "8109",
    ): "64fc2f27d2dddfa968acfacbe5e4e0328071bdc406351ff4a7d18f0b4692c83d",
    (
        "26.928.20755",
        "12246",
    ): "2301fba40bd8fa237ccdb1369363e1deefaf27953da2d767d428225d5e9eedee",
}
EXPECTED_CUA_IDENTIFIER_REPLACEMENTS = 49
EXPECTED_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD = {
    ("26.803.61601", "6396"): 49,
    ("26.810.52044", "6662"): 99,
    ("26.901.22334", "7746"): 49,
    ("26.901.51231", "8109"): 49,
    ("26.928.20755", "12246"): 49,
}
DEFAULT_CUA_SERVICE_LAYOUT = (("Codex Computer Use.app", 17),)
EXPECTED_CUA_SERVICE_LAYOUT_BY_BUILD = {
    ("26.803.61601", "6396"): DEFAULT_CUA_SERVICE_LAYOUT,
    ("26.810.52044", "6662"): (
        ("Codex Computer Use.app", 17),
        ("bin/mac/normal/Codex Computer Use.app", 13),
    ),
    ("26.901.22334", "7746"): DEFAULT_CUA_SERVICE_LAYOUT,
    ("26.901.51231", "8109"): DEFAULT_CUA_SERVICE_LAYOUT,
    ("26.928.20755", "12246"): DEFAULT_CUA_SERVICE_LAYOUT,
}
EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS = 17
EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD = {
    ("26.803.61601", "6396"): 17,
    ("26.810.52044", "6662"): 20,
    ("26.901.22334", "7746"): 16,
    ("26.901.51231", "8109"): 16,
    ("26.928.20755", "12246"): 16,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {PROJECT_VERSION}")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing destination after moving it to a timestamped backup.",
    )
    parser.add_argument(
        "--allow-adhoc-signing",
        action="store_true",
        help="Allow an ad-hoc signature (Appshots and Computer Use may stop working).",
    )
    parser.add_argument(
        "--allow-untested-source",
        action="store_true",
        help="Continue after an explicit version, build, or ASAR hash mismatch.",
    )
    parser.add_argument(
        "--allow-signing-team-change",
        action="store_true",
        help="Replace an existing build signed by a different Apple team.",
    )
    return parser.parse_args()


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def output(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()


def require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(f"required tool not found: {name}")


def resolve_signing_identity(allow_adhoc: bool) -> str:
    configured = os.environ.get("CODEX_MUX_SIGNING_IDENTITY", "").strip()
    if configured:
        return configured
    identities = output(["security", "find-identity", "-v", "-p", "codesigning"])
    available = re.findall(
        r'^\s*\d+\)\s+[0-9A-F]+\s+"([^"]+)"',
        identities,
        re.MULTILINE,
    )
    for prefix in PREFERRED_SIGNING_IDENTITY_PREFIXES:
        for identity in available:
            if identity.startswith(prefix):
                return identity
    if allow_adhoc:
        print(
            "Warning: using an ad-hoc signature; Appshots and Computer Use may be unavailable.",
            file=sys.stderr,
        )
        return "-"
    raise RuntimeError(
        "no team-backed code-signing identity found; set CODEX_MUX_SIGNING_IDENTITY "
        "or explicitly pass --allow-adhoc-signing"
    )


def signing_team_identifier(identity: str) -> str | None:
    if identity == "-":
        return None
    # Apple Development display names may end in a person ID, not a team ID.
    # Let codesign resolve the exact selector (including certificate hashes),
    # then inspect the team it actually writes without exporting a certificate.
    with tempfile.TemporaryDirectory(prefix=".codex-mux-signing-probe-") as temporary:
        probe = Path(temporary) / "signing-probe"
        shutil.copyfile("/usr/bin/true", probe)
        run([
            "codesign", "--force", "--sign", identity,
            "--timestamp=none", str(probe),
        ])
        _, team = signed_code_metadata(probe)
    if team is None or re.fullmatch(r"[A-Z0-9]{10}", team) is None:
        raise RuntimeError("could not determine the selected signing identity's Apple team ID")
    return team



def signed_code_metadata(path: Path) -> tuple[str | None, str | None]:
    result = subprocess.run(
        ["codesign", "--display", "--verbose=4", str(path)],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    details = result.stdout + result.stderr
    identifier_match = re.search(r"^Identifier=(.+)$", details, re.MULTILINE)
    team_match = re.search(r"^TeamIdentifier=(.+)$", details, re.MULTILINE)
    identifier = identifier_match.group(1).strip() if identifier_match else None
    team = team_match.group(1).strip() if team_match else None
    if team == "not set":
        team = None
    return identifier, team


def verify_signed_code(
    path: Path,
    expected_identifier: str,
    expected_team: str | None,
) -> None:
    run(["codesign", "--verify", "--deep", "--strict", str(path)])
    identifier, team = signed_code_metadata(path)
    if identifier != expected_identifier:
        raise RuntimeError(
            f"unexpected signing identifier on {path}: {identifier!r}"
        )
    if team != expected_team:
        raise RuntimeError(f"unexpected signing team on {path}: {team!r}")


def existing_signing_team(path: Path) -> str | None:
    if not path.exists():
        return None
    plist_path = path / "Contents" / "Info.plist"
    if plist_path.is_file():
        try:
            with plist_path.open("rb") as handle:
                recorded = plistlib.load(handle).get("CodexMuxSigningTeamIdentifier")
            if isinstance(recorded, str) and recorded != "":
                return None if recorded == "adhoc" else recorded
        except (OSError, plistlib.InvalidFileException):
            pass
    _, team = signed_code_metadata(path)
    return team


def ensure_components_are_stopped(paths: tuple[Path, ...]) -> None:
    for path in paths:
        if not path.exists():
            continue
        result = subprocess.run(
            ["pgrep", "-f", str(path)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            raise RuntimeError(
                f"quit the running component before replacing it: {path}"
            )


MACH_O_MAGICS = {
    b"\xfe\xed\xfa\xce",  # 32-bit, big endian
    b"\xfe\xed\xfa\xcf",  # 64-bit, big endian
    b"\xce\xfa\xed\xfe",  # 32-bit, little endian
    b"\xcf\xfa\xed\xfe",  # 64-bit, little endian
    b"\xca\xfe\xba\xbe",  # universal binary
    b"\xbe\xba\xfe\xca",  # universal binary, little endian
}


def is_mach_o(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    try:
        with path.open("rb") as handle:
            return handle.read(4) in MACH_O_MAGICS
    except OSError:
        return False


def asar_header_digest(path: Path) -> str:
    """Hash what Electron actually validates: the asar header, not the file.

    ElectronAsarIntegrity records the SHA-256 of the archive's header block.
    Recording the whole-file digest silently passes until a build ships with
    the embedded-asar-integrity fuse enabled, and then every launch aborts
    with "Integrity check failed for asar archive".
    """
    with path.open("rb") as handle:
        prefix = handle.read(16)
        if len(prefix) != 16:
            raise RuntimeError(f"not an asar archive: {path}")
        _, _, _, header_size = struct.unpack("<IIII", prefix)
        header = handle.read(header_size)
    if len(header) != header_size:
        raise RuntimeError(f"truncated asar header: {path}")
    return hashlib.sha256(header).hexdigest()


def arm64_swift_small_string(value: str) -> bytes:
    """Encode the instructions used to materialize a 10-byte Swift string."""
    encoded = value.encode("ascii")
    if len(encoded) != 10:
        raise ValueError("a signing team identifier must contain 10 ASCII bytes")

    def instruction(base: int, immediate: int, register: int, shift: int = 0) -> bytes:
        word = base | ((shift // 16) << 21) | (immediate << 5) | register
        return word.to_bytes(4, "little")

    chunks = [
        int.from_bytes(encoded[index : index + 2], "little")
        for index in range(0, len(encoded), 2)
    ]
    return b"".join(
        (
            instruction(0xD2800000, chunks[0], 0),
            instruction(0xF2800000, chunks[1], 0, 16),
            instruction(0xF2800000, chunks[2], 0, 32),
            instruction(0xF2800000, chunks[3], 0, 48),
            instruction(0xD2800000, chunks[4], 1),
            instruction(0xF2800000, 0xEA00, 1, 48),
        )
    )


def replace_same_length_identifier(
    path: Path, original: str, replacement: str
) -> int:
    """Replace an embedded identifier without changing binary or bundle offsets."""
    original_bytes = original.encode("ascii")
    replacement_bytes = replacement.encode("ascii")
    if len(original_bytes) != len(replacement_bytes):
        raise RuntimeError("replacement identifiers must have the same byte length")
    data = path.read_bytes()
    count = data.count(original_bytes)
    if count:
        path.write_bytes(data.replace(original_bytes, replacement_bytes))
    return count


def computer_use_package(app: Path) -> Path:
    return (
        app
        / "Contents"
        / "Resources"
        / "cua_node"
        / "lib"
        / "node_modules"
        / "@oai"
        / "sky"
    )


def retire_stale_cached_computer_use_app() -> None:
    """Move aside only a prior custom helper copied into the shared Codex home."""
    cached_app = (
        Path.home() / ".codex" / "computer-use" / "Codex Computer Use.app"
    )
    plist_path = cached_app / "Contents" / "Info.plist"
    if not plist_path.is_file():
        return
    try:
        with plist_path.open("rb") as handle:
            bundle_identifier = plistlib.load(handle).get("CFBundleIdentifier")
    except (OSError, plistlib.InvalidFileException):
        return
    if bundle_identifier != COMPUTER_USE_BUNDLE_IDENTIFIER:
        return
    if LAUNCH_SERVICES_REGISTER.is_file():
        run([str(LAUNCH_SERVICES_REGISTER), "-u", str(cached_app)])
    backup = cached_app.with_name(
        f"Codex Computer Use backup-{time.strftime('%Y%m%d-%H%M%S')}"
    )
    cached_app.rename(backup)
    print(f"Stale cached Computer Use helper moved to {backup}")


def patch_computer_use_identity(
    app: Path,
    team_identifier: str | None,
    expected_replacements: int = EXPECTED_CUA_IDENTIFIER_REPLACEMENTS,
    service_layout: tuple[tuple[str, int], ...] = DEFAULT_CUA_SERVICE_LAYOUT,
) -> None:
    """Give the copied CUA service an independent identity and trusted callers."""
    package = computer_use_package(app)
    for profile in package.rglob("embedded.provisionprofile"):
        profile.unlink()

    identifier_replacements = 0
    for candidate in package.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            identifier_replacements += replace_same_length_identifier(
                candidate,
                OPENAI_COMPUTER_USE_BUNDLE_IDENTIFIER,
                COMPUTER_USE_BUNDLE_IDENTIFIER,
            )
    if identifier_replacements != expected_replacements:
        raise RuntimeError(
            "expected "
            f"{expected_replacements} Computer Use identity "
            f"references, found {identifier_replacements}"
        )

    expected_service_paths = {relative for relative, _ in service_layout}
    actual_service_paths = {
        str(candidate.relative_to(package))
        for candidate in package.rglob("Codex Computer Use.app")
        if candidate.is_dir()
    }
    if actual_service_paths != expected_service_paths:
        raise RuntimeError(
            "unexpected Computer Use service layout: "
            f"expected {sorted(expected_service_paths)}, "
            f"found {sorted(actual_service_paths)}"
        )

    for relative, expected_distribution_matches in service_layout:
        service = package / relative
        executable = service / "Contents" / "MacOS" / "SkyComputerUseService"
        if not executable.is_file():
            raise RuntimeError(f"bundled Computer Use service was not found: {relative}")

        plist_path = service / "Contents" / "Info.plist"
        with plist_path.open("rb") as handle:
            info = plistlib.load(handle)
        info["CFBundleIdentifier"] = COMPUTER_USE_BUNDLE_IDENTIFIER
        info["CFBundleDisplayName"] = COMPUTER_USE_DISPLAY_NAME
        info["CFBundleName"] = COMPUTER_USE_DISPLAY_NAME
        for key in list(info):
            if key.startswith("SU"):
                del info[key]
        with plist_path.open("wb") as handle:
            plistlib.dump(info, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)

        if team_identifier is None:
            continue
        binary = executable.read_bytes()
        replacement = arm64_swift_small_string(team_identifier)
        for original_team, description, expected_raw_matches in (
            (OPENAI_INTERNAL_TEAM_IDENTIFIER, "internal", 1),
            (
                OPENAI_DISTRIBUTION_TEAM_IDENTIFIER,
                "distribution",
                expected_distribution_matches,
            ),
        ):
            original = arm64_swift_small_string(original_team)
            match_count = binary.count(original)
            if match_count != 2:
                raise RuntimeError(
                    f"expected two Computer Use {description}-team checks in "
                    f"{relative}, found {match_count}; the official app layout may "
                    "have changed"
                )
            binary = binary.replace(original, replacement)

            raw_original = original_team.encode("ascii")
            raw_replacement = team_identifier.encode("ascii")
            raw_match_count = binary.count(raw_original)
            if raw_match_count != expected_raw_matches:
                raise RuntimeError(
                    f"expected {expected_raw_matches} Computer Use {description}-team "
                    f"constants in {relative}, found {raw_match_count}; the official "
                    "app layout may have changed"
                )
            binary = binary.replace(raw_original, raw_replacement)

        original_bundle_id = b"com.openai.codex\0"
        replacement_bundle_id = DESKTOP_BUNDLE_IDENTIFIER.encode("ascii") + b"\0"
        if len(replacement_bundle_id) != len(original_bundle_id):
            raise RuntimeError(
                "the independent bundle identifier must match the CUA identifier length"
            )
        if binary.count(original_bundle_id) != 1:
            raise RuntimeError(
                f"could not find the Computer Use production bundle ID in {relative}"
            )
        executable.write_bytes(binary.replace(original_bundle_id, replacement_bundle_id))


def patch_asar_computer_use_identity(
    extracted: Path,
    expected_replacements: int = EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS,
) -> None:
    """Keep desktop launch, temp-file, and service references on the new CUA ID."""
    replacements = 0
    for candidate in extracted.rglob("*"):
        if candidate.is_file() and not candidate.is_symlink():
            replacements += replace_same_length_identifier(
                candidate,
                OPENAI_COMPUTER_USE_BUNDLE_IDENTIFIER,
                COMPUTER_USE_BUNDLE_IDENTIFIER,
            )
    if replacements != expected_replacements:
        raise RuntimeError(
            "expected "
            f"{expected_replacements} Computer Use references "
            f"in app.asar, found {replacements}"
        )


def sign_native_code_tree(root: Path, identity: str) -> None:
    """Sign native modules before ASAR records their final sizes."""
    if not root.is_dir():
        return
    for candidate in root.rglob("*"):
        if not is_mach_o(candidate):
            continue
        run(
            [
                "codesign",
                "--force",
                "--sign",
                identity,
                "--timestamp=none",
                "--options",
                "runtime",
                str(candidate),
            ]
        )


TEAM_SCOPED_ENTITLEMENTS = (
    "com.apple.application-identifier",
    "com.apple.developer.aps-environment",
    "com.apple.developer.team-identifier",
    "com.apple.security.application-groups",
    "keychain-access-groups",
)


def sanitized_runtime_entitlements(executable: Path) -> dict[str, object] | None:
    """Keep runtime capabilities while removing the official app's team grants."""
    result = subprocess.run(
        ["codesign", "--display", "--entitlements", ":-", str(executable)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if not result.stdout.strip():
        return None
    try:
        entitlements = plistlib.loads(result.stdout)
    except plistlib.InvalidFileException as error:
        raise RuntimeError(
            f"could not read signing entitlements from {executable}"
        ) from error
    if not isinstance(entitlements, dict):
        raise RuntimeError(f"invalid signing entitlements on {executable}")
    for key in TEAM_SCOPED_ENTITLEMENTS:
        entitlements.pop(key, None)
    return entitlements or None


AUTO_ENTITLEMENTS = object()


def sign_runtime_executable(
    executable: Path,
    identity: str,
    identifier: str | None = None,
    entitlements: dict[str, object] | None | object = AUTO_ENTITLEMENTS,
    runtime: bool = True,
) -> None:
    """Re-sign an embedded runtime without breaking JIT-backed processes."""
    if entitlements is AUTO_ENTITLEMENTS:
        entitlements = sanitized_runtime_entitlements(executable)
    command = [
        "codesign",
        "--force",
        "--sign",
        identity,
        "--timestamp=none",
    ]
    if runtime:
        command.extend(("--options", "runtime"))
    if identifier is None:
        command.append("--preserve-metadata=identifier")
    else:
        command.extend(("--identifier", identifier))
    if entitlements is None:
        run([*command, str(executable)])
        return
    with tempfile.TemporaryDirectory(prefix=".codesign-entitlements-") as temporary:
        entitlements_path = Path(temporary) / "entitlements.plist"
        with entitlements_path.open("wb") as handle:
            plistlib.dump(
                entitlements,
                handle,
                fmt=plistlib.FMT_XML,
                sort_keys=True,
            )
        run([*command, "--entitlements", str(entitlements_path), str(executable)])


def bundle_main_executable(bundle: Path) -> Path | None:
    plist_path = bundle / "Contents" / "Info.plist"
    executable_root = bundle / "Contents" / "MacOS"
    if bundle.suffix == ".framework":
        plist_path = bundle / "Versions" / "Current" / "Resources" / "Info.plist"
        executable_root = bundle / "Versions" / "Current"
    if not plist_path.is_file():
        return None
    with plist_path.open("rb") as handle:
        executable_name = plistlib.load(handle).get("CFBundleExecutable")
    if not isinstance(executable_name, str) or executable_name == "":
        return None
    executable = executable_root / executable_name
    return executable if executable.is_file() else None


def sign_runtime_bundle(
    bundle: Path,
    identity: str,
    identifier: str | None = None,
    entitlements: dict[str, object] | None | object = AUTO_ENTITLEMENTS,
    runtime: bool = True,
) -> None:
    if entitlements is AUTO_ENTITLEMENTS:
        executable = bundle_main_executable(bundle)
        entitlements = (
            sanitized_runtime_entitlements(executable)
            if executable is not None
            else None
        )
    command = [
        "codesign",
        "--force",
        "--sign",
        identity,
        "--timestamp=none",
    ]
    if runtime:
        command.extend(("--options", "runtime"))
    if identifier is not None:
        command.extend(("--identifier", identifier))
    if entitlements is None:
        run([*command, str(bundle)])
        return
    with tempfile.TemporaryDirectory(prefix=".codesign-entitlements-") as temporary:
        entitlements_path = Path(temporary) / "entitlements.plist"
        with entitlements_path.open("wb") as handle:
            plistlib.dump(
                entitlements,
                handle,
                fmt=plistlib.FMT_XML,
                sort_keys=True,
            )
        run([*command, "--entitlements", str(entitlements_path), str(bundle)])


def capture_computer_use_entitlements(
    app: Path,
    service_layout: tuple[tuple[str, int], ...] = DEFAULT_CUA_SERVICE_LAYOUT,
) -> dict[Path, dict[str, object] | None]:
    package = computer_use_package(app)
    entitlements: dict[Path, dict[str, object] | None] = {}
    for relative, _ in service_layout:
        service = package / relative
        if not service.is_dir():
            raise RuntimeError(f"bundled Computer Use service was not found: {relative}")
        entitlements.update(
            {
                executable.relative_to(package): sanitized_runtime_entitlements(
                    executable
                )
                for executable in service.rglob("*")
                if is_mach_o(executable)
            }
        )
    return entitlements


def sign_computer_use_code(
    app: Path,
    identity: str,
    preserved_entitlements: dict[Path, dict[str, object] | None],
    service_layout: tuple[tuple[str, int], ...] = DEFAULT_CUA_SERVICE_LAYOUT,
) -> None:
    """Keep the Computer Use service and its callers on one signing team."""
    resources = app / "Contents" / "Resources"
    package = computer_use_package(app)
    for relative, _ in service_layout:
        service = package / relative
        if not service.is_dir():
            raise RuntimeError(f"bundled Computer Use service was not found: {relative}")

        for executable in sorted(
            (candidate for candidate in service.rglob("*") if is_mach_o(candidate)),
            key=lambda candidate: len(candidate.parts),
            reverse=True,
        ):
            executable_relative = executable.relative_to(package)
            sign_runtime_executable(
                executable,
                identity,
                entitlements=preserved_entitlements.get(executable_relative),
            )

        bundle_suffixes = {".app", ".appex", ".bundle", ".framework", ".xpc"}
        bundles = [
            candidate
            for candidate in service.rglob("*")
            if candidate.is_dir() and candidate.suffix in bundle_suffixes
        ]
        bundles.append(service)
        for bundle in sorted(
            set(bundles),
            key=lambda candidate: len(candidate.parts),
            reverse=True,
        ):
            identifier = (
                COMPUTER_USE_BUNDLE_IDENTIFIER if bundle == service else None
            )
            executable = bundle_main_executable(bundle)
            entitlements = (
                preserved_entitlements.get(executable.relative_to(package))
                if executable is not None
                else None
            )
            sign_runtime_bundle(bundle, identity, identifier, entitlements)
            run(["codesign", "--verify", "--deep", "--strict", str(bundle)])

    for executable_name in ("node", "node_repl"):
        executable = resources / "cua_node" / "bin" / executable_name
        sign_runtime_executable(executable, identity)
    sign_runtime_executable(
        app / "Contents" / "MacOS" / "ChatGPT",
        identity,
        OPENAI_DESKTOP_CODE_IDENTIFIER,
        runtime=False,
    )


def sign_independent_app(
    app: Path,
    identity: str,
    team_identifier: str | None,
    expected_cua_replacements: int = EXPECTED_CUA_IDENTIFIER_REPLACEMENTS,
    service_layout: tuple[tuple[str, int], ...] = DEFAULT_CUA_SERVICE_LAYOUT,
) -> None:
    """Apply one stable identity throughout the modified Electron bundle."""
    computer_use_entitlements = capture_computer_use_entitlements(app, service_layout)
    patch_computer_use_identity(
        app,
        team_identifier,
        expected_cua_replacements,
        service_layout,
    )
    sign_computer_use_code(app, identity, computer_use_entitlements, service_layout)
    sign_bundled_codex(app, identity)
    # Seal every nested desktop bundle from the leaves upward. This keeps the
    # copied app independently valid even when the official source contains a
    # stale nested signature (newer builds add both Chromium frameworks and a
    # Dock tile plug-in).
    embedded_suffixes = {
        ".app",
        ".appex",
        ".bundle",
        ".docktileplugin",
        ".framework",
        ".xpc",
    }
    embedded_roots = tuple(
        root
        for root in (app / "Contents" / "Frameworks", app / "Contents" / "PlugIns")
        if root.is_dir()
    )
    for embedded_executable in sorted(
        (
            candidate
            for root in embedded_roots
            for candidate in root.rglob("*")
            if is_mach_o(candidate)
        ),
        key=lambda candidate: len(candidate.parts),
        reverse=True,
    ):
        sign_runtime_executable(embedded_executable, identity)
    embedded_bundles = sorted(
        (
            candidate
            for root in embedded_roots
            for candidate in root.rglob("*")
            if candidate.is_dir()
            and not candidate.is_symlink()
            and candidate.suffix in embedded_suffixes
        ),
        key=lambda candidate: len(candidate.parts),
        reverse=True,
    )
    for embedded_bundle in embedded_bundles:
        sign_runtime_bundle(embedded_bundle, identity)
    run(
        [
            "codesign",
            "--force",
            "--sign",
            identity,
            "--timestamp=none",
            str(app),
        ]
    )


def bundled_codex_layout(resources: Path) -> tuple[Path, Path | None]:
    """Return the Codex executable the desktop app launches, and its bundle.

    Builds up to 8109 ship a single `Resources/codex`. From 12246 the app runs
    `Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex` directly (the
    `codex-cli/bin/codex` script only forwards to it).
    """
    legacy = resources / "codex"
    if legacy.is_file() and is_mach_o(legacy):
        return legacy, None
    cli_bundle = resources / "codex-cli" / "CodexCLI.app"
    executable = cli_bundle / "Contents" / "MacOS" / "codex"
    if executable.is_file():
        return executable, cli_bundle
    raise RuntimeError("could not find the bundled Codex executable")


def sign_bundled_codex(app: Path, identity: str) -> None:
    """Sign the multiplexer and, in the packaged layout, the whole Codex CLI."""
    resources = app / "Contents" / "Resources"
    legacy = resources / "codex"
    if legacy.is_file() and is_mach_o(legacy):
        run(["codesign", "--force", "--sign", identity, "--timestamp=none", str(legacy)])
        return
    package = resources / "codex-cli"
    for executable in sorted(
        (candidate for candidate in package.rglob("*") if is_mach_o(candidate)),
        key=lambda candidate: len(candidate.parts),
        reverse=True,
    ):
        sign_runtime_executable(executable, identity)
    sign_runtime_bundle(package / "CodexCLI.app", identity)
    run(["codesign", "--verify", "--strict", str(package / "CodexCLI.app")])


def load_or_create_token() -> str:
    DEFAULT_STATE_ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    DEFAULT_STATE_ROOT.chmod(0o700)
    token_path = DEFAULT_STATE_ROOT / "control-token"
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[0-9a-f]{64}", token) is None:
            raise RuntimeError(f"invalid control token at {token_path}")
        token_path.chmod(0o600)
        return token
    token = secrets.token_hex(32)
    descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(token)
    return token


def build_go_program(
    destination: Path,
    package_path: str,
    *,
    goos: str | None = None,
    goarch: str | None = None,
    ldflags: str = "-s -w",
) -> None:
    """Build one Go package from this repository into destination.

    GOOS/GOARCH are only placed in the environment when given so the native
    build (macOS) keeps using whatever the caller's shell already provides.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    env: dict[str, str] | None = None
    if goos is not None or goarch is not None:
        env = dict(os.environ)
        if goos is not None:
            env["GOOS"] = goos
        if goarch is not None:
            env["GOARCH"] = goarch
    run(
        [
            "go",
            "build",
            "-trimpath",
            f"-ldflags={ldflags}",
            "-o",
            str(destination),
            package_path,
        ],
        cwd=PROJECT_ROOT,
        env=env,
    )
    destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def build_proxy(destination: Path) -> None:
    build_go_program(destination, "./cmd/codex-mux")


def install_launcher(app: Path) -> None:
    """Pass Chromium its isolated profile before Electron's main process starts."""
    launcher = app / "Contents" / "MacOS" / "CodexSubscriptionRouterLauncher"
    run(
        [
            "xcrun",
            "clang",
            "-Os",
            "-Wall",
            "-Wextra",
            "-o",
            str(launcher),
            str(PROJECT_ROOT / "native" / "launcher.c"),
        ]
    )


def ensure_asar_tool() -> Path:
    asar = PROJECT_ROOT / "node_modules" / ".bin" / "asar"
    package_manifest = PROJECT_ROOT / "node_modules" / "@electron" / "asar" / "package.json"
    expected = json.loads(
        (PROJECT_ROOT / "package.json").read_text(encoding="utf-8")
    )["devDependencies"]["@electron/asar"]
    if not asar.exists() or not package_manifest.is_file():
        raise RuntimeError("run `npm ci --ignore-scripts` before patching")
    manifest = json.loads(package_manifest.read_text(encoding="utf-8"))
    actual = manifest.get("version")
    if actual != expected:
        raise RuntimeError(
            f"installed @electron/asar is {actual!r}, expected {expected!r}; "
            "run `npm ci --ignore-scripts`"
        )
    require_asar_node_runtime(manifest)
    return asar


def require_asar_node_runtime(manifest: dict) -> None:
    """Fail early when node is too old for the pinned asar, not mid-extract."""
    required = str(manifest.get("engines", {}).get("node", "")).strip()
    minimum = re.match(r">=\s*(\d+)\.(\d+)\.(\d+)", required)
    if not minimum:
        return
    try:
        reported = subprocess.run(
            ["node", "--version"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return
    running = re.match(r"v?(\d+)\.(\d+)\.(\d+)", reported)
    if not running:
        return
    if tuple(map(int, running.groups())) < tuple(map(int, minimum.groups())):
        raise RuntimeError(
            f"node {reported} is too old for @electron/asar {manifest.get('version')} "
            f"(needs {required}); install a newer node, or select one with "
            "`nvm use 22` (or later), and re-run"
        )


def replace_javascript_identifiers(source: str, replacements: dict[str, str]) -> str:
    """Retarget injected source to the minified imports in a supported build."""
    for original, replacement in replacements.items():
        pattern = rf"(?<![A-Za-z0-9_$]){re.escape(original)}(?![A-Za-z0-9_$])"
        source, count = re.subn(pattern, replacement, source)
        if count == 0:
            raise RuntimeError(
                f"could not retarget injected JavaScript identifier {original!r}"
            )
    return source


def patch_renderer(extracted: Path, token: str) -> None:
    webview = extracted / "webview"
    index_path = webview / "index.html"
    index = index_path.read_text(encoding="utf-8")

    connect_anchor = "connect-src &#39;self&#39;"
    if connect_anchor not in index:
        raise RuntimeError("could not find ChatGPT renderer CSP connect-src")
    index = index.replace(
        connect_anchor,
        f"{connect_anchor} http://127.0.0.1:{CONTROL_PORT}",
        1,
    )
    index_path.write_text(index, encoding="utf-8")

    if is_chunked_renderer(webview):
        patch_chunked_renderer(webview, token)
        return

    initial_bundles = list((webview / "assets").glob("app-initial-*.js"))
    if len(initial_bundles) != 1:
        raise RuntimeError(
            f"expected one ChatGPT initial renderer bundle, found {len(initial_bundles)}"
        )
    bundle_path = initial_bundles[0]
    bundle = bundle_path.read_text(encoding="utf-8")
    primary_bundles = list((webview / "assets").glob("app-primary-*.js"))
    primary_menu_marker_7746 = "function ign(e){let t=(0,Fq.c)(223),"
    primary_menu_marker_8109 = "function Ymn(e){let t=(0,sY.c)(223),"
    primary_texts = {
        path: path.read_text(encoding="utf-8") for path in primary_bundles
    }
    menu_bundles_by_marker = {
        marker: [path for path, text in primary_texts.items() if marker in text]
        for marker in (primary_menu_marker_7746, primary_menu_marker_8109)
    }
    for marker, matches in menu_bundles_by_marker.items():
        if len(matches) > 1:
            raise RuntimeError(
                "expected at most one ChatGPT primary renderer menu bundle, "
                f"found {len(matches)}"
            )
    build_7746 = len(menu_bundles_by_marker[primary_menu_marker_7746]) == 1
    build_8109 = len(menu_bundles_by_marker[primary_menu_marker_8109]) == 1
    if build_7746 and build_8109:
        raise RuntimeError(
            "the source app matched two different primary renderer menu layouts"
        )
    # Builds 7746 and 8109 both carry the account menu in a separate
    # app-primary bundle; older builds keep it in the initial bundle.
    menu_in_primary = build_7746 or build_8109
    primary_menu_marker = (
        primary_menu_marker_8109 if build_8109 else primary_menu_marker_7746
    )
    primary_menu_bundles = menu_bundles_by_marker[primary_menu_marker]
    menu_bundle_path = (
        primary_menu_bundles[0] if menu_in_primary else bundle_path
    )
    menu_bundle = (
        menu_bundle_path.read_text(encoding="utf-8") if menu_in_primary else bundle
    )
    if "function CodexMuxAccountMenu(" in bundle or (
        menu_in_primary and "function CodexMuxAccountMenu(" in menu_bundle
    ):
        raise RuntimeError("source app already contains the Codex multiplexer menu")

    component = (PROJECT_ROOT / "ui" / "account-menu.js").read_text(encoding="utf-8")
    component = component.replace("__CODEX_MUX_CONTROL_PORT__", str(CONTROL_PORT))
    component = component.replace("__CODEX_MUX_CONTROL_TOKEN__", token)
    build_6662 = "function Icl(e){let t=(0,Vcl.c)(248)," in bundle
    if build_8109:
        component = replace_javascript_identifiers(
            component,
            {
                "e7": "cY",
                "kXc": "ehn",
                "Lo": "zx",
                "Q": "qv",
                "BW": "QC",
                "QLs": "tq",
                "_H": "xl",
                "S2": "IE",
                "CH": "Sy",
                "jLa": "gV",
                "lt": "Cx",
            },
        )
        component_anchor = primary_menu_marker
    elif build_7746:
        component = replace_javascript_identifiers(
            component,
            {
                "e7": "Iq",
                "kXc": "lgn",
                "Lo": "DO",
                "Q": "_S",
                "BW": "ru",
                "QLs": "jG",
                "_H": "fa",
                "S2": "eD",
                "CH": "Oo",
                "jLa": "jB",
                "lt": "Su",
            },
        )
        component_anchor = primary_menu_marker
    elif build_6662:
        component = replace_javascript_identifiers(
            component,
            {
                "e7": "$5",
                "kXc": "Hcl",
                "Lo": "Fo",
                "BW": "RU",
                "QLs": "E$s",
                "_H": "GV",
                "S2": "E0",
                "CH": "ZV",
                "jLa": "x$a",
                "lt": "ct",
            },
        )
        component_anchor = "function Icl(e){let t=(0,Vcl.c)(248),"
    else:
        component_anchor = "function wXc({sidebarFooter:e,triggerButton:t})"
    component_bundle = menu_bundle if menu_in_primary else bundle
    if component_bundle.count(component_anchor) != 1:
        raise RuntimeError("could not find the native ChatGPT profile menu component")
    component_bundle = component_bundle.replace(
        component_anchor, component + "\n" + component_anchor, 1
    )
    if menu_in_primary:
        menu_bundle = component_bundle
    else:
        bundle = component_bundle

    if menu_in_primary:
        plugin_rpc_literals = (
            ("sendRequest(`app/list`", 2),
            ("sendRequest(`app/installed`", 2),
            ("sendRequest(`app/read`", 2),
            ("sendRequest(`mcpServer/oauth/login`", 1),
            ("sendRequest(`mcpServerStatus/list`", 1),
        )
        if any(
            bundle.count(literal) != expected
            for literal, expected in plugin_rpc_literals
        ):
            raise RuntimeError(
                "could not verify the native Plugins request-to-RPC mapping"
            )
        # The plugin-list timeout constant is renamed between builds.
        plugin_timeout_symbol = "TCn" if build_8109 else "vCn"
        app_server_request_anchor = (
            "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error("
            "`AppServerRequestClient is missing a message dispatcher`);"
            "return e===`config/read`?this.sendConfigReadRequest(t,n):"
            "this.enqueueRequest(e,t,e===`plugin/list`&&n?.timeoutMs==null?"
            f"{{...n,timeoutMs:{plugin_timeout_symbol}}}:n)}}"
        )
        app_server_request_replacement = (
            "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error("
            "`AppServerRequestClient is missing a message dispatcher`);"
            "let r=globalThis.codexMuxScopePluginRequest?.(e,t)??t;"
            "return e===`config/read`?this.sendConfigReadRequest(r,n):"
            "this.enqueueRequest(e,r,e===`plugin/list`&&n?.timeoutMs==null?"
            f"{{...n,timeoutMs:{plugin_timeout_symbol}}}:n)}}"
        )
    else:
        rpc_wrapper = "J9" if build_6662 else "q9"
        status_rpc_wrapper = "q9" if build_6662 else "K9"
        plugin_rpc_mapping_anchors = (
            f'"list-apps":{rpc_wrapper}((e,{{priority:t,source:n,timeoutMs:r,'
            "trace:i,...a})=>e.sendRequest(`app/list`,a,",
            f'"list-installed-apps":{rpc_wrapper}((e,t)=>'
            "e.sendRequest(`app/installed`,t))",
            f'"read-apps":{rpc_wrapper}((e,t)=>e.sendRequest(`app/read`,t))',
            f'"login-mcp-server":{rpc_wrapper}((e,t)=>'
            "e.sendRequest(`mcpServer/oauth/login`,t))",
            f'"list-mcp-server-status":{status_rpc_wrapper}((e,{{priority:t,'
            "source:n,timeoutMs:r,trace:i,...a})=>e.listMcpServers(a,",
            "listMcpServers(e,t){let n=JSON.stringify({options:t,params:e})",
            "let i=this.sendRequest(`mcpServerStatus/list`,e,t);",
        )
        for mapping_anchor in plugin_rpc_mapping_anchors:
            if bundle.count(mapping_anchor) != 1:
                raise RuntimeError(
                    "could not verify the native Plugins request-to-RPC mapping"
                )

    if build_6662 and not menu_in_primary:
        app_server_request_anchor = (
            "function Bp(e,t,n){return n==null?N8e.sendRequest(e,t):"
            "N8e.sendRequest(e,t,n)}"
        )
        app_server_request_replacement = (
            "function Bp(e,t,n){let r=codexMuxScopePluginRequest(e,t);"
            "return n==null?N8e.sendRequest(e,r):N8e.sendRequest(e,r,n)}"
        )
    elif not menu_in_primary:
        app_server_request_anchor = (
            "function gm(e,t,n){return n==null?h6e.sendRequest(e,t):"
            "h6e.sendRequest(e,t,n)}"
        )
        app_server_request_replacement = (
            "function gm(e,t,n){let r=codexMuxScopePluginRequest(e,t);"
            "return n==null?h6e.sendRequest(e,r):h6e.sendRequest(e,r,n)}"
        )
    if bundle.count(app_server_request_anchor) != 1:
        raise RuntimeError("could not find the native app-server request bridge")
    bundle = bundle.replace(
        app_server_request_anchor,
        app_server_request_replacement,
        1,
    )

    if menu_in_primary:
        usage_query_anchor = "return _lr(t,o),o"
        if bundle.count(usage_query_anchor) != 1:
            raise RuntimeError("could not find the native rate-limit status query")
        bundle = bundle.replace(
            usage_query_anchor,
            "o=await globalThis.codexMuxFilterUsageStatus?.(o)??o;"
            "return _lr(t,o),o",
            1,
        )
    else:
        usage_query_pattern = re.compile(
            r"queryKey:\[`rate-limit-status`\],queryFn:async\(\)=>\{try\{return await "
            r"(?P<client>[A-Za-z_$][A-Za-z0-9_$]*)\.safeGet\(`/wham/usage`\)\}"
        )
        usage_query_matches = list(usage_query_pattern.finditer(bundle))
        if len(usage_query_matches) != 1:
            raise RuntimeError("could not find the native rate-limit status query")
        usage_query = usage_query_matches[0]
        bundle = (
            bundle[: usage_query.start()]
            + "queryKey:[`rate-limit-status`],queryFn:async()=>{try{return await "
            "codexMuxFilterUsageStatus(await "
            f"{usage_query.group('client')}.safeGet(`/wham/usage`))}}"
            + bundle[usage_query.end() :]
        )

    profile_query_anchor = (
        "let e=await c_.safeGet(`/wham/profiles/me`)"
        if build_6662
        else (
            "async function wBs(){let e=await _O.safeGet(`/wham/profiles/me`)"
            if build_8109
            else (
                "async function Uzs(){let e=await AO.safeGet(`/wham/profiles/me`)"
                if build_7746
                else "let e=await T_.safeGet(`/wham/profiles/me`)"
            )
        )
    )
    if bundle.count(profile_query_anchor) != 1:
        raise RuntimeError("could not find the native profile stats request")
    if menu_in_primary:
        profile_query_fn = "wBs" if build_8109 else "Uzs"
        profile_query_client = "_O" if build_8109 else "AO"
        profile_query_replacement = (
            f"async function {profile_query_fn}()"
            "{let e=globalThis.codexMuxProfileData?await "
            "globalThis.codexMuxProfileData("
            "globalThis.__codexMuxSelectedProfileAccountId??null):"
            f"await {profile_query_client}.safeGet(`/wham/profiles/me`)"
        )
    else:
        profile_query_replacement = (
            "let e=await codexMuxProfileData("
            "globalThis.__codexMuxSelectedProfileAccountId??null)"
        )
    bundle = bundle.replace(profile_query_anchor, profile_query_replacement, 1)

    native_bundle = menu_bundle if menu_in_primary else bundle
    native_usage_modal_name = (
        "tq"
        if build_8109
        else ("jG" if build_7746 else ("E$s" if build_6662 else "QLs"))
    )
    native_usage_modal_anchor = f"function {native_usage_modal_name}(e){{"
    if native_bundle.count(native_usage_modal_anchor) != 1:
        raise RuntimeError("could not find the native Usage modal component")
    native_bundle = native_bundle.replace(
        native_usage_modal_anchor,
        f"function {native_usage_modal_name}(e){{CodexMuxUseResetAccountState();",
        1,
    )

    if build_8109:
        reset_query_anchor = (
            "function v2i(){let e=(0,VK.c)(1);WR(),Cb(null);let t;return "
            "e[0]===Symbol.for(`react.memo_cache_sentinel`)?"
            "(t={queryKey:[`rate-limit-reset-credits`],queryFn:b2i,select:y2i,"
            "refetchInterval:lD.ONE_MINUTE,staleTime:lD.FIVE_SECONDS},e[0]=t):"
            "t=e[0],Mb(t)}"
        )
        reset_query_replacement = (
            "function v2i(){WR(),Cb(null);let e=window.__codexMuxResetAccountId;"
            "return Mb({queryKey:[`rate-limit-reset-credits`,e??`primary`],"
            "queryFn:e&&globalThis.codexMuxRateLimitResets?"
            "()=>globalThis.codexMuxRateLimitResets(e):b2i,select:y2i,"
            "refetchInterval:lD.ONE_MINUTE,staleTime:lD.FIVE_SECONDS})}"
        )
    elif build_7746:
        reset_query_anchor = (
            "function l2i(){let e=(0,RK.c)(1);HR(),lb(null);let t;return "
            "e[0]===Symbol.for(`react.memo_cache_sentinel`)?"
            "(t={queryKey:[`rate-limit-reset-credits`],queryFn:d2i,select:u2i,"
            "refetchInterval:gD.ONE_MINUTE,staleTime:gD.FIVE_SECONDS},e[0]=t):"
            "t=e[0],vb(t)}"
        )
        reset_query_replacement = (
            "function l2i(){HR(),lb(null);let e=window.__codexMuxResetAccountId;"
            "return vb({queryKey:[`rate-limit-reset-credits`,e??`primary`],"
            "queryFn:e&&globalThis.codexMuxRateLimitResets?"
            "()=>globalThis.codexMuxRateLimitResets(e):d2i,select:u2i,"
            "refetchInterval:gD.ONE_MINUTE,staleTime:gD.FIVE_SECONDS})}"
        )
    elif build_6662:
        reset_query_anchor = (
            "function Ooi(){let e=(0,SI.c)(1),t;return "
            "e[0]===Symbol.for(`react.memo_cache_sentinel`)?"
            "(t={queryKey:[`rate-limit-reset-credits`],queryFn:koi,"
            "refetchInterval:Wp.ONE_MINUTE,staleTime:Wp.FIVE_SECONDS},e[0]=t):"
            "t=e[0],It(t)}"
        )
        reset_query_replacement = (
            "function Ooi(){let e=window.__codexMuxResetAccountId;return It({"
            "queryKey:[`rate-limit-reset-credits`,e??`primary`],"
            "queryFn:e?()=>codexMuxRateLimitResets(e):koi,"
            "refetchInterval:Wp.ONE_MINUTE,staleTime:Wp.FIVE_SECONDS})}"
        )
    else:
        reset_query_anchor = (
            "function l6r(){let e=(0,$F.c)(1),t;return "
            "e[0]===Symbol.for(`react.memo_cache_sentinel`)?"
            "(t={queryKey:[`rate-limit-reset-credits`],queryFn:u6r,"
            "refetchInterval:vm.ONE_MINUTE,staleTime:vm.FIVE_SECONDS},e[0]=t):"
            "t=e[0],Lt(t)}"
        )
        reset_query_replacement = (
            "function l6r(){let e=window.__codexMuxResetAccountId;return Lt({"
            "queryKey:[`rate-limit-reset-credits`,e??`primary`],"
            "queryFn:e?()=>codexMuxRateLimitResets(e):u6r,"
            "refetchInterval:vm.ONE_MINUTE,staleTime:vm.FIVE_SECONDS})}"
        )
    if bundle.count(reset_query_anchor) != 1:
        raise RuntimeError("could not find the native reset-credit query")
    bundle = bundle.replace(
        reset_query_anchor,
        reset_query_replacement,
        1,
    )

    if build_8109:
        reset_mutation_anchor = (
            "function x2i(){let e=(0,VK.c)(3),t=Ob(),n=sD(),r;return "
            "e[0]!==n||e[1]!==t?(r={mutationFn:S2i,onSuccess:(e,r)=>{"
            "let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){"
            "let n=e.code===`reset`?e.credit?.id??i:i;"
            "t.setQueryData([`rate-limit-reset-credits`],e=>i0i(e,a,n))}"
            "Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},"
            "e[0]=n,e[1]=t,e[2]=r):r=e[2],Fb(r)}"
        )
        reset_mutation_replacement = (
            "function x2i(){let e=Ob(),t=sD(),n=window.__codexMuxResetAccountId,"
            "r=[`rate-limit-reset-credits`,n??`primary`];return Fb({"
            "mutationFn:i=>globalThis.codexMuxConsumeRateLimitReset(n,i),"
            "onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;"
            "if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?"
            "n.credit?.id??a:a;e.setQueryData(r,e=>i0i(e,o,t))}"
            "Promise.all([t([`rate-limit-status`]),t(r)])}})}"
        )
    elif build_7746:
        reset_mutation_anchor = (
            "function f2i(){let e=(0,RK.c)(3),t=mb(),n=mD(),r;return "
            "e[0]!==n||e[1]!==t?(r={mutationFn:p2i,onSuccess:(e,r)=>{"
            "let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){"
            "let n=e.code===`reset`?e.credit?.id??i:i;"
            "t.setQueryData([`rate-limit-reset-credits`],e=>Y1i(e,a,n))}"
            "Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},"
            "e[0]=n,e[1]=t,e[2]=r):r=e[2],xb(r)}"
        )
        reset_mutation_replacement = (
            "function f2i(){let e=mb(),t=mD(),n=window.__codexMuxResetAccountId,"
            "r=[`rate-limit-reset-credits`,n??`primary`];return xb({"
            "mutationFn:i=>globalThis.codexMuxConsumeRateLimitReset(n,i),"
            "onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;"
            "if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?"
            "n.credit?.id??a:a;e.setQueryData(r,e=>Y1i(e,o,t))}"
            "Promise.all([t([`rate-limit-status`]),t(r)])}})}"
        )
    elif build_6662:
        reset_mutation_anchor = (
            "function Aoi(){let e=(0,SI.c)(3),t=ct(),n=Uw(),r;return "
            "e[0]!==n||e[1]!==t?(r={mutationFn:joi,onSuccess:(e,r)=>{"
            "let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){"
            "let n=e.code===`reset`?e.credit?.id??i:i;"
            "t.setQueryData([`rate-limit-reset-credits`],e=>eoi(e,a,n))}"
            "Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},"
            "e[0]=n,e[1]=t,e[2]=r):r=e[2],Qt(r)}"
        )
        reset_mutation_replacement = (
            "function Aoi(){let e=ct(),t=Uw(),n=window.__codexMuxResetAccountId,"
            "r=[`rate-limit-reset-credits`,n??`primary`];return Qt({"
            "mutationFn:i=>globalThis.codexMuxConsumeRateLimitReset(n,i),"
            "onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;"
            "if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?"
            "n.credit?.id??a:a;e.setQueryData(r,e=>eoi(e,o,t))}"
            "Promise.all([t([`rate-limit-status`]),t(r)])}})}"
        )
    else:
        reset_mutation_anchor = (
            "function d6r(){let e=(0,$F.c)(3),t=lt(),n=zO(),r;return "
            "e[0]!==n||e[1]!==t?(r={mutationFn:f6r,onSuccess:(e,r)=>{"
            "let{creditId:i}=r,a=e.code;if(a===`reset`||a===`already_redeemed`){"
            "let n=e.code===`reset`?e.credit?.id??i:i;"
            "t.setQueryData([`rate-limit-reset-credits`],e=>F3r(e,a,n))}"
            "Promise.all([n([`rate-limit-status`]),n([`rate-limit-reset-credits`])])}},"
            "e[0]=n,e[1]=t,e[2]=r):r=e[2],$t(r)}"
        )
        reset_mutation_replacement = (
            "function d6r(){let e=lt(),t=zO(),n=window.__codexMuxResetAccountId,"
            "r=[`rate-limit-reset-credits`,n??`primary`];return $t({"
            "mutationFn:i=>globalThis.codexMuxConsumeRateLimitReset(n,i),"
            "onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;"
            "if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?"
            "n.credit?.id??a:a;e.setQueryData(r,e=>F3r(e,o,t))}"
            "Promise.all([t([`rate-limit-status`]),t(r)])}})}"
        )
    if bundle.count(reset_mutation_anchor) != 1:
        raise RuntimeError("could not find the native reset-credit mutation")
    bundle = bundle.replace(
        reset_mutation_anchor,
        reset_mutation_replacement,
        1,
    )

    selected_usage_anchor = "let y=v;if(g!=null){"
    if native_bundle.count(selected_usage_anchor) != 1:
        raise RuntimeError("could not find the native usage-window selection")
    native_bundle = native_bundle.replace(
        selected_usage_anchor,
        "let y=window.__codexMuxSelectedUsageWindows??v;if(g!=null){",
        1,
    )

    if build_8109:
        usage_header_anchor = (
            "let _e;t[46]===he?_e=t[47]:"
            "(_e=(0,eq.jsxs)(mw,{children:[he,ge]}),t[46]=he,t[47]=_e);"
        )
        usage_header_replacement = (
            "let _e=(0,eq.jsxs)(mw,{children:[he,ge,"
            "window.__codexMuxResetAccountSelector??null]});"
        )
    elif build_7746:
        usage_header_anchor = (
            "let ge;t[46]===me?ge=t[47]:"
            "(ge=(0,AG.jsxs)(Gv,{children:[me,he]}),t[46]=me,t[47]=ge);"
        )
        usage_header_replacement = (
            "let ge=(0,AG.jsxs)(Gv,{children:[me,he,"
            "window.__codexMuxResetAccountSelector??null]});"
        )
    elif build_6662:
        usage_header_anchor = (
            "let _e;t[46]===he?_e=t[47]:"
            "(_e=(0,I0.jsxs)(WL,{children:[he,ge]}),t[46]=he,t[47]=_e);"
        )
        usage_header_replacement = (
            "let _e=(0,I0.jsxs)(WL,{children:[he,ge,"
            "window.__codexMuxResetAccountSelector??null]});"
        )
    else:
        usage_header_anchor = (
            "let ve;t[46]===ge?ve=t[47]:"
            "(ve=(0,k2.jsxs)(LL,{children:[ge,_e]}),t[46]=ge,t[47]=ve);"
        )
        usage_header_replacement = (
            "let ve=(0,k2.jsxs)(LL,{children:[ge,_e,"
            "window.__codexMuxResetAccountSelector??null]});"
        )
    if native_bundle.count(usage_header_anchor) != 1:
        raise RuntimeError("could not find the native Usage sheet header")
    native_bundle = native_bundle.replace(
        usage_header_anchor,
        usage_header_replacement,
        1,
    )

    usage_anchor = (
        "usageItems:Dt"
        if build_8109
        else (
            "usageItems:Tt"
            if build_7746
            else ("usageItems:Ct" if build_6662 else "usageItems:Ge")
        )
    )
    if native_bundle.count(usage_anchor) != 1:
        raise RuntimeError("could not find the native ChatGPT usage menu slot")
    native_bundle = native_bundle.replace(
        usage_anchor,
        (
            "usageItems:(0,cY.jsx)(CodexMuxAccountMenu,{})"
            if build_8109
            else (
                "usageItems:(0,Iq.jsx)(CodexMuxAccountMenu,{})"
                if build_7746
                else (
                    "usageItems:(0,$5.jsx)(CodexMuxAccountMenu,{})"
                    if build_6662
                    else "usageItems:(0,e7.jsx)(CodexMuxAccountMenu,{})"
                )
            )
        ),
        1,
    )

    if build_8109:
        open_change_anchors = (
            "triggerButton:jt,onOpenChange:c,children:[F,null]",
            "open:s,onOpenChange:c,contentWidth:`panel`,triggerButton:jt",
        )
        open_change_name = "c"
    elif build_7746:
        open_change_anchors = (
            "triggerButton:kt,onOpenChange:c,children:[F,null]",
            "open:s,onOpenChange:c,contentWidth:`panel`,triggerButton:kt",
        )
        open_change_name = "c"
    elif build_6662:
        open_change_anchors = (
            "triggerButton:Dt,onOpenChange:l,children:P",
            "open:s,onOpenChange:l,contentWidth:`panel`,triggerButton:Dt",
        )
        open_change_name = "l"
    else:
        open_change_anchors = (
            "triggerButton:Ke,onOpenChange:o,children:(0,e7.jsx)(bXc",
            "return(0,e7.jsx)(vH,{open:a,onOpenChange:o,contentWidth:`panel`",
        )
        open_change_name = "o"
    for anchor in open_change_anchors:
        if native_bundle.count(anchor) != 1:
            raise RuntimeError("could not find a native profile menu open-state hook")
        native_bundle = native_bundle.replace(
            anchor,
            anchor.replace(
                f"onOpenChange:{open_change_name}",
                "onOpenChange:CodexMuxProfileMenuOpenChange("
                f"{open_change_name})",
            ),
            1,
        )

    depleted_alert_anchors = (
        "defaultMessage:`You’re out of Codex and Work usage`",
        "defaultMessage:`You’ve used all Codex and Work usage`",
        "defaultMessage:`You’ve reached your usage limit`",
    )
    for depleted_anchor in depleted_alert_anchors:
        if native_bundle.count(depleted_anchor) != 1:
            raise RuntimeError("could not find a native subscription depletion alert")
        native_bundle = native_bundle.replace(
            depleted_anchor,
            "defaultMessage:`All connected subscriptions are depleted`",
            1,
        )
    if menu_in_primary:
        menu_bundle = native_bundle
    else:
        bundle = native_bundle
    bundle_path.write_text(bundle, encoding="utf-8")
    if menu_in_primary:
        menu_bundle_path.write_text(menu_bundle, encoding="utf-8")

    all_profile_bundles = list((webview / "assets").glob("profile-*.js"))
    profile_bundles = (
        [
            path
            for path in all_profile_bundles
            if (
                "avatar:(0,$.jsxs)($.Fragment,{children:["
                "(0,$.jsxs)(`label`,{\"aria-disabled\":"
                f"{'B' if build_8109 else 'L'}.isPending"
            )
            in path.read_text(encoding="utf-8")
        ]
        if menu_in_primary
        else all_profile_bundles
    )
    if len(profile_bundles) != 1:
        raise RuntimeError(
            f"expected one native Profile settings bundle, found {len(profile_bundles)}"
        )
    profile_bundle_path = profile_bundles[0]
    profile_bundle = profile_bundle_path.read_text(encoding="utf-8")
    if build_8109:
        profile_avatar_anchor = (
            "avatar:(0,$.jsxs)($.Fragment,{children:["
            "(0,$.jsxs)(`label`,{\"aria-disabled\":B.isPending,"
            "className:gt(`group relative flex size-20 rounded-full outline-none "
            "focus-within:ring-1 focus-within:ring-ring`,"
        )
        profile_avatar_replacement = (
            "avatar:(0,$.jsxs)($.Fragment,{children:["
            "globalThis.CodexMuxProfileAvatarStack?.("
            "{onSelect:()=>M.refetch()})??null,"
            "(0,$.jsxs)(`label`,{\"aria-disabled\":B.isPending,"
            "className:gt(globalThis.CodexMuxProfileAvatarStack?`hidden`:"
            "`group relative flex size-20 rounded-full outline-none "
            "focus-within:ring-1 focus-within:ring-ring`,"
        )
    elif build_7746:
        profile_avatar_anchor = (
            "avatar:(0,$.jsxs)($.Fragment,{children:["
            "(0,$.jsxs)(`label`,{\"aria-disabled\":L.isPending,"
            "className:re(`group relative flex size-20 rounded-full outline-none "
            "focus-within:ring-1 focus-within:ring-ring`,"
        )
        profile_avatar_replacement = (
            "avatar:(0,$.jsxs)($.Fragment,{children:["
            "globalThis.CodexMuxProfileAvatarStack?.("
            "{onSelect:()=>j.refetch()})??null,"
            "(0,$.jsxs)(`label`,{\"aria-disabled\":L.isPending,"
            "className:re(globalThis.CodexMuxProfileAvatarStack?`hidden`:"
            "`group relative flex size-20 rounded-full outline-none "
            "focus-within:ring-1 focus-within:ring-ring`,"
        )
    elif build_6662:
        profile_avatar_anchor = (
            "avatar:(0,$.jsxs)($.Fragment,{children:["
            "(0,$.jsxs)(`label`,{\"aria-disabled\":z.isPending,"
            "className:Le(`group relative flex size-20 rounded-full outline-none "
            "focus-within:ring-1 focus-within:ring-ring`,"
        )
        profile_avatar_replacement = (
            "avatar:(0,$.jsxs)($.Fragment,{children:["
            "globalThis.CodexMuxProfileAvatarStack?.("
            "{onSelect:()=>M.refetch()})??null,"
            "(0,$.jsxs)(`label`,{\"aria-disabled\":z.isPending,"
            "className:Le(globalThis.CodexMuxProfileAvatarStack?`hidden`:"
            "`group relative flex size-20 rounded-full outline-none "
            "focus-within:ring-1 focus-within:ring-ring`,"
        )
    else:
        profile_avatar_anchor = (
            "children:[(0,$.jsxs)(`div`,{className:`relative mb-4 size-20`,"
            "children:["
        )
        profile_avatar_replacement = (
            "children:[globalThis.CodexMuxProfileAvatarStack?.("
            "{onSelect:()=>A.refetch()})??null,"
            "(0,$.jsxs)(`div`,{className:"
            "globalThis.CodexMuxProfileAvatarStack?"
            "`hidden`:`relative mb-4 size-20`,children:["
        )
    if profile_bundle.count(profile_avatar_anchor) != 1:
        raise RuntimeError("could not find the native Profile avatar")
    profile_bundle = profile_bundle.replace(
        profile_avatar_anchor,
        profile_avatar_replacement,
        1,
    )

    profile_name_anchor = (
        "displayName:Je??(0,$.jsx)(W,{id:`profile.nameFallback`,"
        "defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})"
        if build_8109
        else "displayName:Re??(0,$.jsx)(J,{id:`profile.nameFallback`,"
        "defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})"
        if build_7746
        else (
            "displayName:Ze??(0,$.jsx)(o,{id:`profile.nameFallback`,"
            "defaultMessage:`ChatGPT user`,description:`Fallback profile display name`})"
            if build_6662
            else "className:`flex w-full justify-center`"
        )
    )
    if profile_bundle.count(profile_name_anchor) != 1:
        raise RuntimeError("could not find the native Profile display name")
    profile_bundle = profile_bundle.replace(
        profile_name_anchor,
        (
            "displayName:globalThis.__codexMuxSelectedProfileAccountId?"
            "(Je??(0,$.jsx)(W,{id:`profile.nameFallback`,"
            "defaultMessage:`ChatGPT user`,"
            "description:`Fallback profile display name`})):null"
            if build_8109
            else "displayName:globalThis.__codexMuxSelectedProfileAccountId?"
            "(Re??(0,$.jsx)(J,{id:`profile.nameFallback`,"
            "defaultMessage:`ChatGPT user`,"
            "description:`Fallback profile display name`})):null"
            if build_7746
            else (
                "displayName:globalThis.__codexMuxSelectedProfileAccountId?"
                "(Ze??(0,$.jsx)(o,{id:`profile.nameFallback`,"
                "defaultMessage:`ChatGPT user`,"
                "description:`Fallback profile display name`})):null"
                if build_6662
                else "className:globalThis.__codexMuxSelectedProfileAccountId&&"
                "!A.isFetching?`flex w-full justify-center`:`hidden`"
            )
        ),
        1,
    )
    profile_identity_anchor = (
        "username:qe==null?null:(0,$.jsx)(W,{id:`profile.usernameValue`,"
        "defaultMessage:`@{username}`,"
        "description:`Profile username shown with an at-sign prefix`,"
        "values:{username:qe}})"
        if build_8109
        else "username:Ie==null?null:(0,$.jsx)(J,{id:`profile.usernameValue`,"
        "defaultMessage:`@{username}`,"
        "description:`Profile username shown with an at-sign prefix`,"
        "values:{username:Ie}})"
        if build_7746
        else (
            "username:Ke==null?null:(0,$.jsx)(o,{id:`profile.usernameValue`,"
            "defaultMessage:`@{username}`,"
            "description:`Profile username shown with an at-sign prefix`,"
            "values:{username:Ke}})"
            if build_6662
            else "className:`mt-1 flex min-h-7 items-center gap-1.5 text-base leading-5 "
            "font-normal text-token-text-tertiary`"
        )
    )
    if profile_bundle.count(profile_identity_anchor) != 1:
        raise RuntimeError("could not find the native Profile username and plan badge")
    profile_bundle = profile_bundle.replace(
        profile_identity_anchor,
        (
            "username:globalThis.__codexMuxSelectedProfileAccountId&&qe!=null?"
            "(0,$.jsx)(W,{id:`profile.usernameValue`,"
            "defaultMessage:`@{username}`,"
            "description:`Profile username shown with an at-sign prefix`,"
            "values:{username:qe}}):null"
            if build_8109
            else "username:globalThis.__codexMuxSelectedProfileAccountId&&Ie!=null?"
            "(0,$.jsx)(J,{id:`profile.usernameValue`,"
            "defaultMessage:`@{username}`,"
            "description:`Profile username shown with an at-sign prefix`,"
            "values:{username:Ie}}):null"
            if build_7746
            else (
                "username:globalThis.__codexMuxSelectedProfileAccountId&&Ke!=null?"
                "(0,$.jsx)(o,{id:`profile.usernameValue`,"
                "defaultMessage:`@{username}`,"
                "description:`Profile username shown with an at-sign prefix`,"
                "values:{username:Ke}}):null"
                if build_6662
                else "className:globalThis.__codexMuxSelectedProfileAccountId&&"
                "!A.isFetching?`mt-1 flex min-h-7 items-center gap-1.5 text-base "
                "leading-5 font-normal text-token-text-tertiary`:`hidden`"
            )
        ),
        1,
    )
    profile_bundle_path.write_text(profile_bundle, encoding="utf-8")

    plugin_scope_anchor = (
        "ee=(0,tc.jsxs)(tc.Fragment,{children:[H,U]})"
        if build_6662
        else "action:F,children:w})"
    )
    plugin_scope_replacement = (
        "ee=(0,tc.jsxs)(tc.Fragment,{children:[globalThis.CodexMuxPluginScope?.()??null,H,U]})"
        if build_6662
        else "action:F,children:[globalThis.CodexMuxPluginScope?.()??null,w]})"
    )
    plugin_bundle_glob = "plugins-page-*.js" if build_6662 else "plugins-settings-*.js"
    plugin_bundles = [
        path
        for path in (webview / "assets").glob(plugin_bundle_glob)
        if plugin_scope_anchor in path.read_text(encoding="utf-8")
    ]
    if len(plugin_bundles) != 1:
        raise RuntimeError(
            f"expected one native Plugins settings bundle, found {len(plugin_bundles)}"
        )
    plugin_bundle_path = plugin_bundles[0]
    plugin_bundle = plugin_bundle_path.read_text(encoding="utf-8")
    if plugin_bundle.count(plugin_scope_anchor) != 1:
        raise RuntimeError("could not find the native Plugins settings content")
    plugin_bundle = plugin_bundle.replace(
        plugin_scope_anchor,
        plugin_scope_replacement,
        1,
    )
    plugin_bundle_path.write_text(plugin_bundle, encoding="utf-8")

    thread_component_anchor = (
        "function uT(e){let t=(0,pT.c)(43),"
        if menu_in_primary
        else (
            "function bE(){let e=(0,SE.c)(1)"
            if build_6662
            else "function bE(){let e=(0,wE.c)(57)"
        )
    )
    thread_bundles = [
        path
        for path in (webview / "assets").glob("local-conversation-thread-*.js")
        if thread_component_anchor in path.read_text(encoding="utf-8")
    ]
    if len(thread_bundles) != 1:
        raise RuntimeError(
            f"expected one local conversation renderer bundle, found {len(thread_bundles)}"
        )
    thread_bundle_path = thread_bundles[0]
    thread_bundle = thread_bundle_path.read_text(encoding="utf-8")
    thread_component = (PROJECT_ROOT / "ui" / "thread-subscription.js").read_text(
        encoding="utf-8"
    )
    thread_component = thread_component.replace(
        "__CODEX_MUX_CONTROL_PORT__", str(CONTROL_PORT)
    )
    thread_component = thread_component.replace("__CODEX_MUX_CONTROL_TOKEN__", token)
    if menu_in_primary:
        thread_component = replace_javascript_identifiers(
            thread_component,
            {
                "$n": "zd",
                "sr": "Gi",
                "TE": "nT",
                "zE": "mT",
                "K": "Q",
            },
        )
        summary_component = "mT"
    elif build_6662:
        thread_component = replace_javascript_identifiers(
            thread_component,
            {
                "$n": "jf",
                "sr": "Pa",
                "TE": "jy",
                "zE": "CE",
                "K": "q",
            },
        )
        summary_component = "CE"
    else:
        summary_component = "zE"
    if thread_bundle.count(thread_component_anchor) != 1:
        raise RuntimeError("could not find the native thread summary sources component")
    thread_bundle = thread_bundle.replace(
        thread_component_anchor,
        thread_component + "\n" + thread_component_anchor,
        1,
    )
    summary_children_anchor = (
        "children:[d,f,p,m,h,g,_,v,y,b,x,S,C,w,T,E,D]"
        if menu_in_primary
        else "children:[c,l,u,d,f,p,m,h,g,_,v,y,b,x]"
    )
    if thread_bundle.count(summary_children_anchor) != 1:
        raise RuntimeError("could not find the native thread summary section list")
    summary_children_replacement = (
        "children:[d,f,p,m,h,g,_,v,y,b,x,S,(0,"
        f"{summary_component}.jsx)(CodexMuxThreadSubscription,{{}}),"
        "C,w,T,E,D]"
        if menu_in_primary
        else "children:[c,l,u,d,f,(0,"
        f"{summary_component}.jsx)(CodexMuxThreadSubscription,{{}}),"
        "p,m,h,g,_,v,y,b,x]"
    )
    thread_bundle = thread_bundle.replace(
        summary_children_anchor,
        summary_children_replacement,
        1,
    )
    thread_bundle_path.write_text(thread_bundle, encoding="utf-8")


def _sub_once(text: str, pattern: str, replacement, what: str, flags: int = 0) -> str:
    """Apply a regex replacement that must match exactly once."""
    compiled = re.compile(pattern, flags)
    matches = list(compiled.finditer(text))
    if len(matches) != 1:
        raise RuntimeError(f"could not find {what} (found {len(matches)} matches)")
    return compiled.sub(replacement, text, count=1)


def _match_once(text: str, pattern: str, what: str, flags: int = 0) -> re.Match[str]:
    matches = list(re.finditer(pattern, text, flags))
    if len(matches) != 1:
        raise RuntimeError(f"could not find {what} (found {len(matches)} matches)")
    return matches[0]


def _asset_containing(assets: Path, pattern: str, marker: str, what: str) -> Path:
    matches = [
        path
        for path in assets.glob(pattern)
        if marker in path.read_text(encoding="utf-8")
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {what}, found {len(matches)}")
    return matches[0]


def _export_name(bundle: str, local: str) -> str:
    match = _match_once(
        bundle, rf"(?<![\w$]){re.escape(local)} as ([\w$]+)(?![\w$])", f"export of {local}"
    )
    return match.group(1)


def _local_for_export(bundle: str, exported: str) -> str:
    match = _match_once(
        bundle, rf"(?<![\w$])([\w$]+) as {re.escape(exported)}(?![\w$])", f"local for {exported}"
    )
    return match.group(1)


def is_chunked_renderer(webview: Path) -> bool:
    """Builds from 12246 load the profile menu from a lazy chunk."""
    return any(
        "ambientUsage:" in path.read_text(encoding="utf-8")
        and "{sidebarFooter:" in path.read_text(encoding="utf-8")
        for path in (webview / "assets").glob("profile-dropdown-items-*.js")
    )


def patch_chunked_usage_state(modal: str) -> str:
    """Own selection inside the loaded modal, beyond the cached lazy wrapper."""
    ident = r"[\w$]+"
    component = _match_once(
        modal, rf"export\{{({ident}) as RateLimitResetModal\}}", "usage modal export"
    ).group(1)
    modal = _sub_once(
        modal, rf"function {re.escape(component)}\(e\)\{{",
        f"function {component}(e){{globalThis.CodexMuxUseResetAccountState();",
        "the loaded Usage sheet state",
    )
    # The compiler cached the child solely on native props, excluding our
    # selection. Recreate it and remount its reset form when the account changes.
    # The compiler's memo-cache array is named by the minifier (`t` on macOS
    # builds, `n` on the Windows build 12947), so it is captured once and every
    # later use must be that same identifier.
    return _sub_once(
        modal,
        rf"let ({ident})=({ident}),({ident});return (?P<cache>{ident})\[\d+\].{{0,500}}?"
        rf"\(\3=\(0,(?P<jsx>{ident})\.jsx\)\((?P<component>{ident}),"
        rf"(?P<props>\{{defaultResetCreditsOpen:{ident},errorMessage:{ident},"
        rf"initialAvailableCount:{ident},isResetting:{ident},onClose:{ident},"
        rf"onResetCredit:\1\}})\),(?P=cache)\[\d+\].{{0,500}}?\):\3=(?P=cache)\[\d+\],\3",
        lambda m: (
            f"let {m.group(1)}={m.group(2)};return (0,{m.group('jsx')}.jsx)"
            f"({m.group('component')},{m.group('props')},window.__codexMuxResetAccountId??`primary`)"
        ),
        "the Usage sheet selection propagation",
    )


def patch_chunked_renderer(webview: Path, token: str) -> None:
    """Patch builds (12246+) whose menu, usage sheet and helpers are split.

    Anchors are matched structurally and the minified names are read from
    the build itself, so a rebuild with renamed identifiers still patches
    while any structural change fails closed.
    """
    assets = webview / "assets"
    ident = r"[\w$]+"
    initial_path = single_asset(assets, "app-initial-*.js", "initial renderer bundle")
    shared_path = single_asset(assets, "app-shared-*.js", "shared renderer bundle")
    menu_path = _asset_containing(
        assets, "profile-dropdown-items-*.js", "ambientUsage:", "profile menu chunk"
    )
    modal_path = _asset_containing(
        assets, "modal-impl-*.js", "as RateLimitResetModal", "usage sheet chunk"
    )
    initial = initial_path.read_text(encoding="utf-8")
    shared = shared_path.read_text(encoding="utf-8")
    menu = menu_path.read_text(encoding="utf-8")
    modal = modal_path.read_text(encoding="utf-8")
    for text, where in ((initial, "initial"), (menu, "menu")):
        if "CodexMuxAccountMenu" in text:
            raise RuntimeError(f"source app already contains the multiplexer ({where})")

    # --- profile menu chunk: resolve the names the injected menu uses ---
    header = _match_once(
        menu,
        rf"function ({ident})\(e\)\{{let {ident}=\(0,{ident}\.c\)\(\d+\),"
        rf"\{{sidebarFooter:{ident},ambientUsage:{ident},open:{ident},onClose:{ident}\}}=e,"
        rf"({ident})=({ident})\(({ident})\),",
        "the native profile menu component",
    )
    scope_variable, scope_hook, scope_key = header.group(2, 3, 4)
    menu_start = header.start()
    menu_body = menu[menu_start : menu_start + 200_000]
    opener = _match_once(
        menu_body,
        rf"({ident})\({re.escape(scope_variable)},({ident}),\{{defaultResetCreditsOpen:!0",
        "the native usage sheet opener",
    )
    open_modal, usage_modal = opener.group(1, 2)
    react = _match_once(
        menu_body[:4000], rf"\(0,({ident})\.useState\)", "React in the profile menu"
    ).group(1)
    item = re.search(
        rf"\(0,({ident})\.jsx\)\(({ident}),\{{(?:leftIconAsset|LeftIcon):{ident},"
        r'"aria-label":' + ident + r",className:`opacity-50`",
        menu_body,
    )
    if item is None:
        raise RuntimeError("could not find the native profile menu item")
    jsx, menu_item = item.group(1, 2)
    namespaces = set(re.findall(rf"\(0,{re.escape(jsx)}\.jsx\)\(({ident})\.Separator,", menu))
    namespaces |= set(re.findall(rf"({ident})\.FlyoutSubmenuItem", menu))
    if len(namespaces) != 1:
        raise RuntimeError(f"could not identify the native menu namespace: {sorted(namespaces)}")
    menu_namespace = namespaces.pop()
    image_resolver = _match_once(
        menu_body,
        rf"({ident})\({ident}\?\.profile_picture_url\?\?null\)",
        "the profile image resolver",
    ).group(1)

    # React Query's useQueryClient is not imported by the menu chunk; add it.
    query_client_local = _match_once(
        shared,
        rf"({ident})=e=>\{{let t={ident}\.useContext\({ident}\);if\(e\)return e;"
        r"if\(!t\)throw Error\(`No QueryClient set",
        "React Query's client hook",
    ).group(1)
    query_client_export = _export_name(shared, query_client_local)

    component = (PROJECT_ROOT / "ui" / "account-menu.js").read_text(encoding="utf-8")
    component = component.replace("__CODEX_MUX_CONTROL_PORT__", str(CONTROL_PORT))
    component = component.replace("__CODEX_MUX_CONTROL_TOKEN__", token)
    component = replace_javascript_identifiers(
        component,
        {
            "e7": jsx,
            "kXc": react,
            "Lo": scope_hook,
            "Q": scope_key,
            "BW": open_modal,
            "QLs": usage_modal,
            "_H": menu_item,
            "S2": "CodexMuxUsageIcon",
            "CH": menu_namespace,
            "jLa": image_resolver,
            "lt": "CodexMuxUseQueryClient",
        },
    )
    menu = menu[:menu_start] + component + "\n" + menu[menu_start:]
    # Added after the component so the offset above stays valid.
    shared_name = shared_path.name
    menu = _sub_once(
        menu,
        rf'import\{{(?=[^}}]*\}}from"\./{re.escape(shared_name)}")',
        f"import{{{query_client_export} as CodexMuxUseQueryClient,",
        "the menu chunk's shared import",
    )
    menu = _sub_once(
        menu,
        rf"usageItems:{ident}\}}\)",
        f"usageItems:(0,{jsx}.jsx)(CodexMuxAccountMenu,{{}})}})",
        "the native usage menu slot",
    )
    menu_path.write_text(menu, encoding="utf-8")

    # The menu chunk is lazy, but its helpers pool usage for every surface.
    # Load it at startup and let early requests wait for it.
    initial = (
        initial.rstrip()
        + "\n;globalThis.__codexMuxReady=new Promise(e=>setTimeout(e,0))"
        f'.then(()=>import("./{menu_path.name}")).catch(()=>{{}});\n'
    )

    # --- shared bundle: plugin scoping and pooled usage ---
    plugin_rpc_literals = (
        "sendRequest(`app/list`",
        "sendRequest(`app/installed`",
        "sendRequest(`app/read`",
        "sendRequest(`mcpServer/oauth/login`",
        "sendRequest(`mcpServerStatus/list`",
    )
    for literal in plugin_rpc_literals:
        if literal not in shared and literal not in initial:
            raise RuntimeError("could not verify the native Plugins request-to-RPC mapping")
    shared = _sub_once(
        shared,
        r"async sendRequest\(e,t,n\)\{if\(this\.dispatchMessage==null\)throw Error\("
        r"`AppServerRequestClient is missing a message dispatcher`\);"
        r"return e===`config/read`\?this\.sendConfigReadRequest\(t,n\):"
        r"this\.enqueueRequest\(e,t,",
        "async sendRequest(e,t,n){if(this.dispatchMessage==null)throw Error("
        "`AppServerRequestClient is missing a message dispatcher`);"
        "t=globalThis.codexMuxScopePluginRequest?.(e,t)??t;"
        "return e===`config/read`?this.sendConfigReadRequest(t,n):"
        "this.enqueueRequest(e,t,",
        "the native app-server request bridge",
    )
    shared = _sub_once(
        shared,
        rf"try\{{return ({ident})\(await ({ident})\.safeGet\(`/wham/usage`,"
        r"(\{additionalHeaders:\{[^{}]*\},signal:t\})\)\)\}catch",
        lambda m: (
            f"try{{let r={m.group(1)}(await {m.group(2)}.safeGet(`/wham/usage`,"
            f"{m.group(3)}));await globalThis.__codexMuxReady;"
            "return(await globalThis.codexMuxFilterUsageStatus?.(r))??r}catch"
        ),
        "the native rate-limit status request",
    )
    shared = _sub_once(
        shared,
        rf"let (\w)=({ident})\(({ident})\((\w)\.usage\),(\w)\.getQueryData\((\w)\)\);",
        lambda m: (
            m.group(0)
            + f"{m.group(1)}=globalThis.codexMuxFilterUsageStatusSync?.({m.group(1)})"
            f"??{m.group(1)};"
        ),
        "the native usage stream snapshot",
    )

    # --- initial bundle: profile stats, reset credits, usage sheet hook ---
    initial = _sub_once(
        initial,
        rf"async function ({ident})\(\)\{{let e=await ({ident})\.safeGet\(`/wham/profiles/me`\)",
        lambda m: (
            f"async function {m.group(1)}(){{await globalThis.__codexMuxReady;"
            "let e=globalThis.codexMuxProfileData?await globalThis.codexMuxProfileData("
            "globalThis.__codexMuxSelectedProfileAccountId??null):"
            f"await {m.group(2)}.safeGet(`/wham/profiles/me`)"
        ),
        "the native profile stats request",
    )
    initial = _sub_once(
        initial,
        rf"function ({ident})\(\)\{{let e=\(0,{ident}\.c\)\(1\);({ident}\(\),{ident}\(null\));"
        r"let t;return e\[0\]===Symbol\.for\(`react\.memo_cache_sentinel`\)\?"
        rf"\(t=\{{queryKey:\[`rate-limit-reset-credits`\],queryFn:({ident}),select:({ident}),"
        rf"refetchInterval:({ident})\.ONE_MINUTE,staleTime:\5\.FIVE_SECONDS\}},e\[0\]=t\):"
        rf"t=e\[0\],({ident})\(t\)\}}",
        lambda m: (
            f"function {m.group(1)}(){{{m.group(2)};let e=window.__codexMuxResetAccountId;"
            f"return {m.group(6)}({{queryKey:[`rate-limit-reset-credits`,e??`primary`],"
            "queryFn:e&&globalThis.codexMuxRateLimitResets?"
            f"()=>globalThis.codexMuxRateLimitResets(e):{m.group(3)},select:{m.group(4)},"
            f"refetchInterval:{m.group(5)}.ONE_MINUTE,staleTime:{m.group(5)}.FIVE_SECONDS}})}}"
        ),
        "the native reset-credit query",
    )
    initial = _sub_once(
        initial,
        rf"function ({ident})\(\)\{{let e=\(0,{ident}\.c\)\(3\),t=({ident})\(\),n=({ident})\(\),r;"
        rf"return e\[0\]!==n\|\|e\[1\]!==t\?\(r=\{{mutationFn:({ident}),onSuccess:\(e,r\)=>\{{"
        r"let\{creditId:i\}=r,a=e\.code;if\(a===`reset`\|\|a===`already_redeemed`\)\{"
        r"let n=e\.code===`reset`\?e\.credit\?\.id\?\?i:i;"
        rf"t\.setQueryData\(\[`rate-limit-reset-credits`\],e=>({ident})\(e,a,n\)\)\}}"
        r"Promise\.all\(\[n\(\[`rate-limit-status`\]\),n\(\[`rate-limit-reset-credits`\]\)\]\)\}\},"
        rf"e\[0\]=n,e\[1\]=t,e\[2\]=r\):r=e\[2\],({ident})\(r\)\}}",
        lambda m: (
            f"function {m.group(1)}(){{let e={m.group(2)}(),t={m.group(3)}(),"
            "n=window.__codexMuxResetAccountId,"
            f"r=[`rate-limit-reset-credits`,n??`primary`];return {m.group(6)}({{"
            "mutationFn:i=>globalThis.codexMuxConsumeRateLimitReset(n,i),"
            "onSuccess:(n,i)=>{let{creditId:a}=i,o=n.code;"
            "if(o===`reset`||o===`already_redeemed`){let t=o===`reset`?"
            f"n.credit?.id??a:a;e.setQueryData(r,e=>{m.group(5)}(e,o,t))}}"
            "Promise.all([t([`rate-limit-status`]),t(r)])}})}"
        ),
        "the native reset-credit mutation",
    )
    initial = _sub_once(
        initial,
        r"defaultMessage:`You’re out of usage`",
        "defaultMessage:`All connected subscriptions are depleted`",
        "the native usage depletion alert",
    )
    initial_path.write_text(initial, encoding="utf-8")
    shared_path.write_text(shared, encoding="utf-8")

    # --- usage sheet chunk: per-subscription windows and picker ---
    modal = patch_chunked_usage_state(modal)
    modal = _sub_once(
        modal,
        rf"let ({ident})=({ident});if\(({ident})!=null\)\{{let e;return t\[7\]!==",
        lambda m: (
            f"let {m.group(1)}=window.__codexMuxSelectedUsageWindows??{m.group(2)};"
            f"if({m.group(3)}!=null){{let e;return t[7]!=="
        ),
        "the native usage-window selection",
    )
    modal = _sub_once(
        modal,
        rf"let ({ident});t\[(\d+)\]===Symbol\.for\(`react\.memo_cache_sentinel`\)\?"
        rf"\(\1=\(0,({ident})\.jsx\)\(({ident}),\{{children:"
        r"(\(0,\3\.jsx\)\(" + ident + r",\{title:.{0,600}?"
        r"description:`Heading for the Codex usage limit modal`\}\)\}\)\}\)\}\))"
        r"\}\),t\[\2\]=\1\):\1=t\[\2\];",
        lambda m: (
            f"let {m.group(1)}=(0,{m.group(3)}.jsxs)({m.group(4)},{{children:["
            f"{m.group(5)},window.__codexMuxResetAccountSelector??null]}});"
        ),
        "the native Usage sheet header",
    )
    modal_path.write_text(modal, encoding="utf-8")

    # --- profile page: combined view with per-subscription avatars ---
    profile_path = _asset_containing(
        assets, "profile-*.js", "profile.nameFallback", "native Profile page bundle"
    )
    profile = profile_path.read_text(encoding="utf-8")
    query_client = _match_once(
        profile,
        rf"({ident})\.getQueryData\({ident}\.queryKey\)\?\.profile_details\.username",
        "the Profile page query client",
    ).group(1)
    profile = _sub_once(
        profile,
        r"avatar:\(0,\$\.jsxs\)\(\$\.Fragment,\{children:\[\(0,\$\.jsxs\)\(`div`,"
        r"(\{\"aria-disabled\":" + ident + r",(?:(?!className:).){0,300})"
        rf"className:({ident})\(`group relative flex rounded-full outline-none`,",
        lambda m: (
            "avatar:(0,$.jsxs)($.Fragment,{children:["
            "globalThis.CodexMuxProfileAvatarStack?.({onSelect:()=>"
            f"{query_client}.invalidateQueries({{queryKey:[`profile`]}})}})??null,"
            f"(0,$.jsxs)(`div`,{m.group(1)}className:{m.group(2)}("
            "globalThis.CodexMuxProfileAvatarStack?`hidden`:"
            "`group relative flex rounded-full outline-none`,"
        ),
        "the native Profile avatar",
    )
    profile = _sub_once(
        profile,
        rf"(?<=[,;{{])({ident})=({ident})\?\?(\(0,\$\.jsx\)\({ident},\{{id:`profile\.nameFallback`,"
        r"defaultMessage:`ChatGPT user`,description:`Fallback profile display name`\}\))",
        lambda m: (
            f"{m.group(1)}=globalThis.__codexMuxSelectedProfileAccountId?"
            f"({m.group(2)}??{m.group(3)}):null"
        ),
        "the native Profile display name",
    )
    profile = _sub_once(
        profile,
        rf"(?<=[,;{{])({ident})=({ident})==null\?null:(?=\(0,\$\.jsxs\)\(\$\.Fragment,"
        r"\{children:\[\(0,\$\.jsx\)\(" + ident + r",\{id:`profile\.username\.prefix`)",
        lambda m: (
            f"{m.group(1)}={m.group(2)}==null||"
            "!globalThis.__codexMuxSelectedProfileAccountId?null:"
        ),
        "the native Profile username",
    )
    profile_path.write_text(profile, encoding="utf-8")

    # --- Plugins settings: subscription picker for connections ---
    plugin_path = _asset_containing(
        assets,
        "plugins-settings-*.js",
        "plugins-settings.browseDirectory",
        "native Plugins settings bundle",
    )
    plugins = plugin_path.read_text(encoding="utf-8")
    plugins = _sub_once(
        plugins,
        rf"(\(0,{ident}\.jsx\)\({ident},\{{title:{ident},subtitle:{ident},"
        rf"action:{ident},children:)({ident})\}}\)",
        lambda m: f"{m.group(1)}[globalThis.CodexMuxPluginScope?.()??null,{m.group(2)}]}})",
        "the native Plugins settings content",
    )
    plugin_path.write_text(plugins, encoding="utf-8")

    # --- chat summary: the subscription that owns this chat ---
    thread_path = _asset_containing(
        assets,
        "local-conversation-thread-*.js",
        ",onForceShow:",
        "local conversation renderer bundle",
    )
    thread = thread_path.read_text(encoding="utf-8")
    summary = _match_once(
        thread,
        rf"function ({ident})\(e\)\{{let t=\(0,{ident}\.c\)\(\d+\),\{{isHidden:{ident},"
        rf"onForceShow:[^}}]*\}}=e,{ident}={ident}!==void 0&&{ident},"
        rf"({ident})=({ident})\(({ident})\),",
        "the native thread summary component",
    )
    route_hook, route_atom = summary.group(3, 4)
    summary_body = thread[summary.start() : summary.start() + 20_000]
    activity = re.search(rf"\(0,({ident})\.jsx\)\(({ident})\.Activity,", summary_body)
    if activity is None:
        raise RuntimeError("could not find React in the thread summary")
    thread_jsx, thread_react = activity.group(1, 2)
    sections = set(re.findall(rf"({ident})\.Section,\{{sectionKey:", thread))
    if len(sections) != 1:
        raise RuntimeError(f"could not identify the thread summary sections: {sorted(sections)}")
    thread_component = (PROJECT_ROOT / "ui" / "thread-subscription.js").read_text(
        encoding="utf-8"
    )
    thread_component = thread_component.replace("__CODEX_MUX_CONTROL_PORT__", str(CONTROL_PORT))
    thread_component = thread_component.replace("__CODEX_MUX_CONTROL_TOKEN__", token)
    thread_component = replace_javascript_identifiers(
        thread_component,
        {
            "$n": route_hook,
            "sr": route_atom,
            "TE": thread_react,
            "zE": thread_jsx,
            "K": sections.pop(),
        },
    )
    list_pattern = (
        rf"\(0,{re.escape(thread_jsx)}\.jsxs\)\({re.escape(thread_jsx)}\.Fragment,"
        rf"\{{children:\[((?:{ident},){{4}}{ident})((?:,{ident}){{4}})\]\}}\)"
    )
    summary_end = summary.start() + len(summary_body)
    head, tail = thread[: summary.start()], thread[summary.start() : summary_end]
    tail = _sub_once(
        tail,
        list_pattern,
        lambda m: (
            f"(0,{thread_jsx}.jsxs)({thread_jsx}.Fragment,{{children:[{m.group(1)},"
            f"(0,{thread_jsx}.jsx)(CodexMuxThreadSubscription,{{}}){m.group(2)}]}})"
        ),
        "the native thread summary section list",
    )
    thread = head + thread_component + "\n" + tail + thread[summary_end:]
    thread_path.write_text(thread, encoding="utf-8")


def _import_alias_source(bundle: str, alias: str) -> str:
    """Return the exported name a chunk imported under `alias`."""
    for names, _ in re.findall(r'import\{([^}]*)\}from"(\./[^"]+)"', bundle):
        for part in names.split(","):
            match = re.fullmatch(r"([\w$]+) as ([\w$]+)", part.strip())
            if match and match.group(2) == alias:
                return match.group(1)
    raise RuntimeError(f"could not resolve the import behind {alias}")


def single_asset(assets: Path, pattern: str, what: str) -> Path:
    matches = list(assets.glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {what}, found {len(matches)}")
    return matches[0]


def disable_updater_lifecycle(extracted: Path) -> None:
    """Keep every updater entry point (launch gate, menu, IPC) from starting Sparkle."""
    updater_anchor = (
        "initializeUpdater(){return this.options.enableUpdater?"
        "(this.updaterInitialization??=this.initializeUpdaterOnce(),"
        "this.updaterInitialization):Promise.resolve()}"
    )
    matches = [
        path
        for path in (extracted / ".vite" / "build").glob("*.js")
        if updater_anchor in path.read_text(encoding="utf-8")
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected one desktop updater lifecycle bundle, found {len(matches)}"
        )
    bundle_path = matches[0]
    bundle = bundle_path.read_text(encoding="utf-8")
    if bundle.count(updater_anchor) != 1:
        raise RuntimeError("could not find the desktop updater lifecycle")
    bundle = bundle.replace(
        updater_anchor,
        "initializeUpdater(){return this.lastUnavailableReason="
        f"`disabled by {DESKTOP_PROFILE_NAME}`,Promise.resolve()}}",
        1,
    )
    bundle_path.write_text(bundle, encoding="utf-8")


def single_bundle(extracted: Path, pattern: str, what: str) -> Path:
    """Return the one .vite/build bundle matching pattern or fail closed."""
    matches = list((extracted / ".vite" / "build").glob(pattern))
    if len(matches) != 1:
        raise RuntimeError(f"expected one {what}, found {len(matches)}")
    return matches[0]


def macos_desktop_profile_prelude(installed_computer_use_app: Path) -> str:
    """Environment the copied app needs before its userData path is redirected.

    The Computer Use helper socket and app paths point at this build's own
    helper so the copy never shares the official app's helper or its grants.
    """
    computer_use_pipe = json.dumps(str(DEFAULT_STATE_ROOT / "computer-use.sock"))
    computer_use_app = json.dumps(str(installed_computer_use_app))
    return (
        f"process.env.SKY_CUA_SERVICE_NATIVE_PIPE_PATH={computer_use_pipe};"
        f"process.env.SKY_CUA_SERVICE_PATH={computer_use_app};"
        f"process.env.CODEX_ELECTRON_COMPUTER_USE_APP_PATH={computer_use_app};"
        "process.env.CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH=`1`;"
    )


def isolate_desktop_profile(extracted: Path, prelude: str) -> None:
    """Give the copied Electron app its own user-data and single-instance scope.

    prelude is JavaScript inserted immediately before the userData rewrite;
    each platform supplies the environment its helper layout needs.
    """
    bootstrap_path = single_bundle(extracted, "bootstrap-*.js", "ChatGPT bootstrap bundle")
    bootstrap = bootstrap_path.read_text(encoding="utf-8")
    profile_pattern = re.compile(
        r"(?P<electron>[A-Za-z_$][\w$]*)\.app\.setPath\("
        r"`userData`,[A-Za-z_$][\w$]*\(\{"
        r"appDataPath:(?P=electron)\.app\.getPath\(`appData`\),"
        r"buildFlavor:[^,}]+,env:process\.env\}\)\)"
    )

    def replacement(match: re.Match[str]) -> str:
        electron = match.group("electron")
        return (
            prelude
            + f"{electron}.app.setPath(`userData`,"
            f"{electron}.app.getPath(`appData`)+`/{DESKTOP_PROFILE_NAME}`)"
        )

    bootstrap, replacements = profile_pattern.subn(replacement, bootstrap, count=1)
    if replacements != 1:
        raise RuntimeError("could not isolate the copied ChatGPT desktop profile")

    # The copied app must never replace itself with an unpatched official update.
    updater_pattern = re.compile(
        r"await [A-Za-z_$][\w$]*\.initialize\(\);"
        r"(?=(?:try\{)?let\{runMainAppStartup:)"
    )
    bootstrap, updater_replacements = updater_pattern.subn("", bootstrap, count=1)
    if updater_replacements == 0:
        # Build 12246 folds the call into the condition that picks the Windows
        # runtime: `try{if(await n.initialize(),r&&a&&...,r||i){`.
        comma_updater_pattern = re.compile(
            r"(?<=let s=\{phase:`bootstrap-import-main`\};try\{if\()"
            r"await [A-Za-z_$][\w$]*\.initialize\(\),"
        )
        bootstrap, updater_replacements = comma_updater_pattern.subn(
            "", bootstrap, count=1
        )
    if updater_replacements != 1:
        raise RuntimeError("could not disable updates in the copied ChatGPT app")
    bootstrap_path.write_text(bootstrap, encoding="utf-8")
    disable_updater_lifecycle(extracted)


def pin_managed_computer_use(
    extracted: Path, installed_computer_use_app: Path
) -> None:
    """Point the managed Computer Use service at this build's own helper app.

    macOS only: the helper, its socket, and the strict tool instruction are
    specific to the Swift service the macOS patcher re-signs.
    """
    main_path = single_bundle(extracted, "main-*.js", "ChatGPT desktop main bundle")
    main = main_path.read_text(encoding="utf-8")
    managed_service_pattern = re.compile(
        r"(?P<prefix>[A-Za-z_$][\w$]*=new [A-Za-z_$][\w$]*\()"
        r"[A-Za-z_$][\w$]*\([A-Za-z_$][\w$]*\.codexHome\)"
        r"(?P<suffix>,\{onServiceAvailable:)"
    )
    main, managed_service_replacements = managed_service_pattern.subn(
        lambda match: (
            match.group("prefix")
            + json.dumps(str(installed_computer_use_app))
            + match.group("suffix")
        ),
        main,
        count=1,
    )
    if managed_service_replacements != 1:
        raise RuntimeError(
            "could not pin the managed Computer Use service to its installed app"
        )

    computer_use_instruction = (
        "Control desktop apps on macOS through Computer Use."
    )
    strict_computer_use_instruction = (
        "Control desktop apps on macOS through Computer Use via node_repl and "
        "@oai/sky only. Never use shell commands, open, AppleScript, osascript, "
        "JXA, System Events, or CGEvent synthesis for computer interactions or "
        "as a fallback. If Computer Use is unavailable, report the failure "
        "instead of using another automation method."
    )
    if main.count(computer_use_instruction) != 1:
        raise RuntimeError("could not find the Computer Use tool instruction")
    main = main.replace(
        computer_use_instruction,
        strict_computer_use_instruction,
        1,
    )
    main_path.write_text(main, encoding="utf-8")


def install_ui_test_bridge(extracted: Path) -> None:
    """Ship the loopback UI test bridge; it only starts under CODEX_MUX_UI_TESTS=1."""
    main_path = single_bundle(extracted, "main-*.js", "ChatGPT desktop main bundle")
    main = main_path.read_text(encoding="utf-8")
    ui_test_bridge = extracted / ".vite" / "build" / "ui-test-bridge.cjs"
    shutil.copy2(PROJECT_ROOT / "ui" / "ui-test-bridge.cjs", ui_test_bridge)
    main += (
        "\n;if(process.env.CODEX_MUX_UI_TESTS===`1`)"
        "require(require(`node:path`).join(__dirname,`ui-test-bridge.cjs`)).start();"
    )
    main_path.write_text(main, encoding="utf-8")


def patch_desktop_profile(
    extracted: Path, installed_computer_use_app: Path
) -> None:
    """Give the copied Electron app its own user-data and single-instance scope."""
    isolate_desktop_profile(
        extracted, macos_desktop_profile_prelude(installed_computer_use_app)
    )
    pin_managed_computer_use(extracted, installed_computer_use_app)
    install_ui_test_bridge(extracted)


def patch_electron_integrity_digest(app: Path, integrity: dict) -> None:
    """Update Electron's enabled v1 plist digest before re-signing the framework.

    Newer Electron authenticates ElectronAsarIntegrity itself against a slot
    in __DATA_CONST,__asar_integrity. Hash the sorted path/algorithm/hash UTF-8
    strings exactly as integrity_digest.mm does; keep validation enabled.
    Older frameworks have no slot or leave it unused.
    """
    hasher = hashlib.sha256()
    # NSString's literal ordering compares UTF-16 code units.
    for key in sorted(integrity, key=lambda value: value.encode("utf-16-be")):
        for value in (key, integrity[key]["algorithm"], integrity[key]["hash"]):
            hasher.update(value.encode("utf-8"))
    digest = hasher.digest()
    sentinel = b"AGbevlPCksUGKNL8TSn7wGmJEuJsXb2A"
    for framework in sorted((app / "Contents" / "Frameworks").glob("*.framework")):
        binary = framework / framework.stem
        if not binary.is_file():
            continue
        with binary.open("r+b") as handle:
            if os.fstat(handle.fileno()).st_size == 0:
                continue
            with mmap.mmap(handle.fileno(), 0) as data:
                offsets = []
                start = 0
                while (offset := data.find(sentinel, start)) != -1:
                    start = offset + len(sentinel)
                    if offset + 66 > len(data):
                        raise RuntimeError(f"truncated Electron integrity digest slot: {binary}")
                    used, version = data[offset + 32:offset + 34]
                    if used == 0:
                        continue
                    if used != 1 or version != 1:
                        raise RuntimeError(f"unsupported Electron integrity digest slot {used}/{version}: {binary}")
                    offsets.append(offset + 34)
                # Validate all architecture slots before modifying this binary.
                for offset in offsets:
                    data[offset:offset + 32] = digest
                if offsets:
                    data.flush()


def patch_info_plist(
    app: Path,
    asar_path: Path,
    team_identifier: str | None,
) -> None:
    plist_path = app / "Contents" / "Info.plist"
    with plist_path.open("rb") as handle:
        info = plistlib.load(handle)
    info["CFBundleDisplayName"] = "Codex Subscription Router"
    info["CFBundleName"] = "Codex Subscription Router"
    # A distinct identifier keeps Launch Services and external Computer Use from
    # confusing this independently signed copy with the official ChatGPT app.
    info["CFBundleIdentifier"] = DESKTOP_BUNDLE_IDENTIFIER
    info["CFBundleExecutable"] = "CodexSubscriptionRouterLauncher"
    info["BundleSigningBaseName"] = "CodexSubscriptionRouter"
    info["CodexMuxSigningTeamIdentifier"] = team_identifier or "adhoc"
    info["CrProductDirName"] = DESKTOP_PROFILE_NAME
    for key in list(info):
        if key.startswith("SU"):
            del info[key]
    info["SUEnableAutomaticChecks"] = False
    info["SUAllowsAutomaticUpdates"] = False
    for url_type in info.get("CFBundleURLTypes", []):
        schemes = url_type.get("CFBundleURLSchemes", [])
        url_type["CFBundleURLSchemes"] = [
            "codex-subscription-router" if value == "codex" else value for value in schemes
        ]
    digest = asar_header_digest(asar_path)
    info["ElectronAsarIntegrity"] = {
        "Resources/app.asar": {"algorithm": "SHA256", "hash": digest}
    }
    with plist_path.open("wb") as handle:
        plistlib.dump(info, handle, fmt=plistlib.FMT_BINARY, sort_keys=False)
    patch_electron_integrity_digest(app, info["ElectronAsarIntegrity"])


def patch_app(
    source: Path,
    destination: Path,
    force: bool,
    allow_adhoc_signing: bool,
    allow_untested_source: bool,
    allow_signing_team_change: bool,
) -> None:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_dir() or not (source / "Contents" / "Resources" / "app.asar").is_file():
        raise RuntimeError(f"not a ChatGPT app bundle: {source}")
    if source == destination:
        raise RuntimeError(
            "source and destination must be different; "
            "the original app is never patched in place"
        )
    if destination.exists() and not force:
        raise RuntimeError(
            f"destination exists: {destination} "
            "(pass --force to create a recoverable backup)"
        )

    source_plist = source / "Contents" / "Info.plist"
    with source_plist.open("rb") as handle:
        source_info = plistlib.load(handle)
    source_version = str(source_info.get("CFBundleShortVersionString", "unknown"))
    source_build = str(source_info.get("CFBundleVersion", "unknown"))
    source_asar = source / "Contents" / "Resources" / "app.asar"
    source_asar_hash = hashlib.sha256(source_asar.read_bytes()).hexdigest()
    expected_asar_hash = TESTED_SOURCE_BUILDS.get((source_version, source_build))
    print(
        f"Source ChatGPT version: {source_version} ({source_build}), "
        f"app.asar {source_asar_hash}"
    )
    if expected_asar_hash != source_asar_hash and not allow_untested_source:
        raise RuntimeError(
            "the source version, build, or app.asar hash is not approved; "
            "review the upstream change or pass --allow-untested-source"
        )
    if expected_asar_hash != source_asar_hash:
        print(
            "Warning: continuing with an untested official ChatGPT build; "
            "the patch will continue only while every expected anchor matches.",
            file=sys.stderr,
        )

    for tool in ("codesign", "ditto", "go", "npm", "security", "xcrun"):
        require_tool(tool)
    asar = ensure_asar_tool()
    token = load_or_create_token()
    signing_identity = resolve_signing_identity(allow_adhoc_signing)
    team_identifier = signing_team_identifier(signing_identity)
    if destination.exists():
        installed_team = existing_signing_team(destination)
        if installed_team != team_identifier and not allow_signing_team_change:
            raise RuntimeError(
                "the selected signing team differs from the installed build; "
                "reuse the prior identity or pass --allow-signing-team-change"
            )
    destination.parent.mkdir(parents=True, exist_ok=True)
    installed_computer_use_app = destination.parent / COMPUTER_USE_APP_NAME
    if force:
        ensure_components_are_stopped((destination, installed_computer_use_app))

    with tempfile.TemporaryDirectory(prefix=".codex-subscription-router-", dir=destination.parent) as temporary:
        temporary_path = Path(temporary)
        staged_app = temporary_path / destination.name
        staged_computer_use_app = temporary_path / COMPUTER_USE_APP_NAME
        extracted = temporary_path / "asar"
        proxy = temporary_path / "codex-mux"

        print("Building multiplexer…")
        build_proxy(proxy)
        print("Copying ChatGPT.app…")
        run(["ditto", str(source), str(staged_app)])
        install_launcher(staged_app)

        resources = staged_app / "Contents" / "Resources"
        original_asar = resources / "app.asar"
        print("Patching desktop profile and renderer…")
        run([str(asar), "extract", str(original_asar), str(extracted)])
        expected_cua_replacements = (
            EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD.get(
                (source_version, source_build),
                EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS,
            )
        )
        patch_asar_computer_use_identity(extracted, expected_cua_replacements)
        patch_desktop_profile(extracted, installed_computer_use_app)
        patch_renderer(extracted, token)
        sign_native_code_tree(extracted, signing_identity)
        repacked_asar = temporary_path / "app.asar"
        run(
            [
                str(asar),
                "pack",
                "--unpack-dir",
                ASAR_UNPACK_DIRECTORIES,
                str(extracted),
                str(repacked_asar),
            ]
        )
        asar_listing = output([str(asar), "list", "--is-pack", str(repacked_asar)])
        required_unpacked_module = (
            "unpack : /node_modules/better-sqlite3/build/Release/"
            "better_sqlite3.node"
        )
        if required_unpacked_module not in asar_listing:
            raise RuntimeError("native ASAR modules were not kept unpacked")
        shutil.copy2(repacked_asar, original_asar)
        repacked_unpacked = temporary_path / "app.asar.unpacked"
        if not repacked_unpacked.is_dir():
            raise RuntimeError("ASAR pack did not produce its unpacked native tree")
        shutil.copytree(
            repacked_unpacked,
            resources / "app.asar.unpacked",
            dirs_exist_ok=True,
        )

        bundled_codex, codex_cli_bundle = bundled_codex_layout(resources)
        real_codex = bundled_codex.with_name("codex.real")
        if real_codex.exists():
            raise RuntimeError("source app already contains codex.real")
        if codex_cli_bundle is not None:
            # The provisioning profile grants OpenAI's keychain group to the
            # bundle's main executable; the re-signed copy has neither.
            for profile in codex_cli_bundle.rglob("embedded.provisionprofile"):
                profile.unlink()
        bundled_codex.rename(real_codex)
        shutil.copy2(proxy, bundled_codex)
        bundled_codex.chmod(0o755)

        patch_info_plist(staged_app, original_asar, team_identifier)
        print(f"Signing independent app copy with {signing_identity}…")
        expected_cua_identity_replacements = (
            EXPECTED_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD.get(
                (source_version, source_build),
                EXPECTED_CUA_IDENTIFIER_REPLACEMENTS,
            )
        )
        cua_service_layout = EXPECTED_CUA_SERVICE_LAYOUT_BY_BUILD.get(
            (source_version, source_build),
            DEFAULT_CUA_SERVICE_LAYOUT,
        )
        sign_independent_app(
            staged_app,
            signing_identity,
            team_identifier,
            expected_cua_identity_replacements,
            cua_service_layout,
        )
        verify_signed_code(
            staged_app,
            DESKTOP_BUNDLE_IDENTIFIER,
            team_identifier,
        )
        verify_signed_code(
            staged_app / "Contents" / "MacOS" / "ChatGPT",
            OPENAI_DESKTOP_CODE_IDENTIFIER,
            team_identifier,
        )
        bundled_computer_use_app = (
            computer_use_package(staged_app) / "Codex Computer Use.app"
        )
        run(
            [
                "ditto",
                str(bundled_computer_use_app),
                str(staged_computer_use_app),
            ]
        )
        verify_signed_code(
            staged_computer_use_app,
            COMPUTER_USE_BUNDLE_IDENTIFIER,
            team_identifier,
        )

        backup_suffix = time.strftime("%Y%m%d-%H%M%S")
        backup_directory = DEFAULT_STATE_ROOT / "backups" / backup_suffix
        app_backup = backup_directory / destination.name
        helper_backup = backup_directory / installed_computer_use_app.name
        had_app = destination.exists()
        had_helper = installed_computer_use_app.exists()
        if had_app or had_helper:
            backup_directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            backup_directory.parent.chmod(0o700)
            backup_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        try:
            if had_app:
                destination.rename(app_backup)
                print(f"Existing copy moved to {app_backup}")
            if had_helper:
                installed_computer_use_app.rename(helper_backup)
                print(f"Existing Computer Use helper moved to {helper_backup}")
            staged_app.rename(destination)
            staged_computer_use_app.rename(installed_computer_use_app)
        except OSError:
            failed_directory = backup_directory / "failed-install"
            failed_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if destination.exists():
                destination.rename(failed_directory / destination.name)
            if installed_computer_use_app.exists():
                installed_computer_use_app.rename(
                    failed_directory / installed_computer_use_app.name
                )
            if app_backup.exists():
                app_backup.rename(destination)
            if helper_backup.exists():
                helper_backup.rename(installed_computer_use_app)
            raise

    if LAUNCH_SERVICES_REGISTER.is_file():
        run(
            [
                str(LAUNCH_SERVICES_REGISTER),
                "-f",
                str(destination),
                str(installed_computer_use_app),
            ]
        )
    retire_stale_cached_computer_use_app()

    print(destination)
    print(installed_computer_use_app)


def main() -> int:
    args = parse_args()
    try:
        patch_app(
            args.source,
            args.destination,
            args.force,
            args.allow_adhoc_signing,
            args.allow_untested_source,
            args.allow_signing_team_change,
        )
    except (RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f"patch failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
