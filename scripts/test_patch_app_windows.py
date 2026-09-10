"""Windows patcher tests; run on any OS, no Windows tools or official app required."""
import contextlib
import errno
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

import patch_app
import patch_app_windows as win


def make_app(root: Path, *executables: str, unpacked: dict | None = None) -> Path:
    """Create a fake Electron install: resources/app.asar plus top-level exes."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "resources").mkdir(exist_ok=True)
    (root / "resources" / "app.asar").write_bytes(b"asar")
    for name in executables:
        (root / name).write_bytes(b"MZ")
    if unpacked is not None:
        base = root / "resources" / "app.asar.unpacked" / "node_modules"
        for package in unpacked:
            (base / package).mkdir(parents=True)
    return root


class ImportTests(unittest.TestCase):
    def test_import_is_side_effect_free(self):
        # Importing must not touch the registry, spawn processes, or need
        # Windows environment variables: the tests import it on Linux/macOS.
        # This process imported the module long before the test ran, so a
        # fresh interpreter does the import with every process-spawning entry
        # point and os.getlogin replaced by a failing stub and the home
        # directory pointed at an empty temporary one, then reports whether
        # winreg was loaded and what appeared under that home.
        self.assertTrue(hasattr(win, "patch_app"))
        self.assertIs(win.shared, patch_app)
        home = Path(tempfile.mkdtemp())
        probe = textwrap.dedent(
            f"""
            import json, os, subprocess, sys

            def forbidden(*args, **kwargs):
                raise AssertionError("import spawned a process or looked up the login")

            for name in ("run", "check_output", "check_call", "call", "Popen"):
                setattr(subprocess, name, forbidden)
            os.getlogin = forbidden
            os.system = forbidden
            sys.path.insert(0, {str(win.SCRIPT_DIRECTORY)!r})
            # Blame only modules the import itself pulls in: on Windows the
            # interpreter's own start-up can already have loaded winreg, so a
            # bare "winreg in sys.modules" would fail there for nothing the
            # patcher did.
            before = set(sys.modules)
            import patch_app_windows
            print(json.dumps({{
                "winreg": "winreg" in set(sys.modules) - before,
                "home": sorted(os.listdir({str(home)!r})),
            }}))
            """
        )
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
        completed = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, env=env
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout)
        self.assertFalse(report["winreg"])
        self.assertEqual(report["home"], [])

    def test_patch_app_is_windows_only(self):
        if sys.platform == "win32":
            self.skipTest("this guard only fires on non-Windows hosts")
        with self.assertRaises(RuntimeError) as caught:
            win.patch_app(None, None, False, False, None, None, True)
        self.assertIn("runs on Windows", str(caught.exception))

    def test_tested_builds_table_is_empty_until_verified(self):
        self.assertEqual(win.TESTED_WINDOWS_SOURCE_BUILDS, {})


class DestinationTests(unittest.TestCase):
    def test_default_destination_under_local_app_data(self):
        self.assertEqual(
            win.default_destination({"LOCALAPPDATA": "/lad"}),
            Path("/lad") / "Programs" / "Codex Subscription Router",
        )

    def test_environment_lookup_is_case_insensitive(self):
        self.assertEqual(
            win.default_destination({"LocalAppData": "/lad"}),
            Path("/lad") / "Programs" / "Codex Subscription Router",
        )
        with self.assertRaises(RuntimeError):
            win.default_destination({})


class SourceDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.lad = self.root / "Local"
        self.pf = self.root / "Program Files"
        self.env = {"LOCALAPPDATA": str(self.lad), "ProgramFiles": str(self.pf)}

    def test_candidates_include_only_existing_directories(self):
        make_app(self.lad / "Programs" / "ChatGPT", "ChatGPT.exe")
        (self.pf / "Codex").mkdir(parents=True)
        self.assertEqual(
            win.candidate_source_directories(self.env),
            [self.lad / "Programs" / "ChatGPT", self.pf / "Codex"],
        )

    def test_squirrel_newest_version_is_selected(self):
        make_app(self.lad / "ChatGPT" / "app-1.0.0", "ChatGPT.exe")
        make_app(self.lad / "ChatGPT" / "app-1.2.0", "ChatGPT.exe")
        make_app(self.lad / "ChatGPT" / "app-1.10.0", "ChatGPT.exe")
        (self.lad / "ChatGPT" / "app-notes.txt").write_text("x")
        self.assertEqual(
            win.candidate_source_directories(self.env),
            [self.lad / "ChatGPT" / "app-1.10.0"],
        )
        self.assertEqual(
            win.discover_source(self.env, None), self.lad / "ChatGPT" / "app-1.10.0"
        )

    def test_missing_environment_variables_are_skipped(self):
        self.assertEqual(win.candidate_source_directories({}), [])

    def test_exactly_one_qualifying_install(self):
        make_app(self.lad / "Programs" / "ChatGPT", "ChatGPT.exe")
        (self.pf / "ChatGPT").mkdir(parents=True)  # exists, no app.asar
        self.assertEqual(
            win.discover_source(self.env, None), self.lad / "Programs" / "ChatGPT"
        )

    def test_ambiguous_or_absent_installs_fail_listing_examined_paths(self):
        make_app(self.lad / "Programs" / "ChatGPT", "ChatGPT.exe")
        make_app(self.lad / "Programs" / "Codex", "Codex.exe")
        with self.assertRaises(RuntimeError) as caught:
            win.discover_source(self.env, None)
        self.assertIn("found 2", str(caught.exception))
        self.assertIn(str(self.lad / "Programs" / "Codex"), str(caught.exception))
        with self.assertRaises(RuntimeError) as caught:
            win.discover_source({}, None)
        self.assertIn("found 0", str(caught.exception))
        self.assertIn("(none)", str(caught.exception))

    def test_explicit_source_must_be_an_electron_app(self):
        app = make_app(self.root / "elsewhere" / "ChatGPT", "ChatGPT.exe")
        self.assertEqual(win.discover_source(self.env, app), app)
        with self.assertRaises(RuntimeError):
            win.discover_source(self.env, self.root / "elsewhere")

    def test_store_installs_are_refused(self):
        app = make_app(
            self.pf / "WindowsApps" / "OpenAI.ChatGPT_1.0_x64__abc", "ChatGPT.exe"
        )
        with self.assertRaises(RuntimeError) as caught:
            win.discover_source(self.env, app)
        self.assertIn("Store/MSIX installs cannot be copied", str(caught.exception))
        with self.assertRaises(RuntimeError):
            win.discover_source(self.env, self.root / "windowsapps" / "ChatGPT")

    def test_is_electron_app_directory(self):
        self.assertFalse(win.is_electron_app_directory(self.root))
        self.assertTrue(win.is_electron_app_directory(make_app(self.root / "a")))


class ElectronExecutableTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app(
            Path(tempfile.mkdtemp()) / "ChatGPT",
            "ChatGPT.exe",
            "Uninstall ChatGPT.exe",
            "unins000.exe",
            "Update.exe",
            "SquirrelSetup.exe",
            "elevate.exe",
            "Codex Subscription Router.exe",
            "readme.txt",
        )
        (self.app / "resources" / "nested.exe").write_bytes(b"MZ")

    def test_uninstallers_updaters_and_the_launcher_are_excluded(self):
        self.assertEqual(
            [entry.name for entry in win.electron_executables(self.app)], ["ChatGPT.exe"]
        )

    def test_case_insensitive_suffix(self):
        (self.app / "Other.EXE").write_bytes(b"MZ")
        self.assertEqual(
            [entry.name for entry in win.electron_executables(self.app)],
            ["ChatGPT.exe", "Other.EXE"],
        )
        with self.assertRaises(RuntimeError) as caught:
            win.select_electron_executable(self.app, None)
        self.assertIn("ChatGPT.exe, Other.EXE", str(caught.exception))

    def test_select_single_or_override(self):
        self.assertEqual(win.select_electron_executable(self.app, None).name, "ChatGPT.exe")
        self.assertEqual(
            win.select_electron_executable(self.app, "Update.exe").name, "Update.exe"
        )
        with self.assertRaises(RuntimeError):
            win.select_electron_executable(self.app, "Missing.exe")
        with self.assertRaises(RuntimeError):
            win.select_electron_executable(self.app, "resources/nested.exe")


class CodexExecutableTests(unittest.TestCase):
    def setUp(self):
        self.app = make_app(Path(tempfile.mkdtemp()) / "ChatGPT", "ChatGPT.exe")
        self.nested = self.app / "resources" / "app.asar.unpacked" / "node_modules" / "@oai" / "codex" / "bin"
        self.nested.mkdir(parents=True)

    def test_nested_discovery_is_case_insensitive(self):
        (self.nested / "Codex.EXE").write_bytes(b"MZ")
        (self.app / "codex.real.exe").write_bytes(b"MZ")
        (self.app / "Codex Subscription Router.exe").write_bytes(b"MZ")
        self.assertEqual(
            win.locate_codex_executable(self.app, None), self.nested / "Codex.EXE"
        )

    def test_duplicates_and_absence_fail_listing_matches(self):
        with self.assertRaises(RuntimeError) as caught:
            win.locate_codex_executable(self.app, None)
        self.assertIn("found 0", str(caught.exception))
        (self.nested / "codex.exe").write_bytes(b"MZ")
        (self.app / "resources" / "codex.exe").write_bytes(b"MZ")
        with self.assertRaises(RuntimeError) as caught:
            win.locate_codex_executable(self.app, None)
        self.assertIn("found 2", str(caught.exception))
        self.assertIn("resources/codex.exe", str(caught.exception).replace("\\", "/"))

    def test_override_relative_path(self):
        (self.app / "resources" / "codex.exe").write_bytes(b"MZ")
        self.assertEqual(
            win.locate_codex_executable(self.app, "resources/codex.exe"),
            self.app / "resources" / "codex.exe",
        )
        with self.assertRaises(RuntimeError):
            win.locate_codex_executable(self.app, "resources/missing.exe")

    def test_override_is_confined_to_the_source_directory(self):
        # pathlib drops the left operand for an anchored right operand and keeps
        # "..": either would make the swap run on the official install.
        elsewhere = self.app.parent / "elsewhere" / "codex.exe"
        elsewhere.parent.mkdir(parents=True)
        elsewhere.write_bytes(b"MZ")
        (self.app / "resources" / "codex.exe").write_bytes(b"MZ")
        for bad in (
            str(elsewhere),
            "\\codex.exe",
            "/codex.exe",
            "C:\\codex.exe",
            "C:codex.exe",
            "\\\\server\\share\\codex.exe",
            "../elsewhere/codex.exe",
            "resources/../../elsewhere/codex.exe",
            "",
        ):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError) as caught:
                win.locate_codex_executable(self.app, bad)
            self.assertIn("--codex-executable", str(caught.exception))
        self.assertEqual(elsewhere.read_bytes(), b"MZ")

    def test_override_must_name_codex_exe(self):
        # The multiplexer is copied over whatever the override names; pointing
        # it at another file would leave the app spawning the official codex.
        (self.app / "resources" / "other.exe").write_bytes(b"MZ")
        with self.assertRaises(RuntimeError) as caught:
            win.locate_codex_executable(self.app, "resources/other.exe")
        self.assertIn("must name codex.exe", str(caught.exception))


class UnpackGlobTests(unittest.TestCase):
    def test_scoped_and_plain_packages(self):
        app = make_app(
            Path(tempfile.mkdtemp()) / "a",
            unpacked={"better-sqlite3": {}, "node-pty": {}, "@worklouder/lib": {}},
        )
        self.assertEqual(
            win.unpack_globs(app), "node_modules/{@worklouder,better-sqlite3,node-pty}"
        )

    def test_single_package_has_no_braces(self):
        app = make_app(Path(tempfile.mkdtemp()) / "a", unpacked={"node-pty": {}})
        self.assertEqual(win.unpack_globs(app), "node_modules/node-pty")

    def test_no_unpacked_tree(self):
        self.assertIsNone(win.unpack_globs(make_app(Path(tempfile.mkdtemp()) / "a")))

    def test_unknown_layouts_fail_closed(self):
        app = make_app(Path(tempfile.mkdtemp()) / "a", unpacked={"node-pty": {}})
        (app / "resources" / "app.asar.unpacked" / "bin").mkdir()
        with self.assertRaises(RuntimeError) as caught:
            win.unpack_globs(app)
        self.assertIn("bin", str(caught.exception))
        empty = make_app(Path(tempfile.mkdtemp()) / "b")
        (empty / "resources" / "app.asar.unpacked" / "node_modules").mkdir(parents=True)
        with self.assertRaises(RuntimeError):
            win.unpack_globs(empty)


LISTING = """\
pack   : /.vite
pack   : /.vite/build
pack   : /.vite/build/main-a.js
pack   : /node_modules
unpack : /node_modules/better-sqlite3
unpack : /node_modules/better-sqlite3/build/Release/better_sqlite3.node
pack   : /node_modules/react/index.js
unpack : /node_modules/node-pty/build/Release/pty.node
"""


class AsarListingTests(unittest.TestCase):
    def test_parse_realistic_listing(self):
        all_paths, unpacked = win.parse_asar_listing(LISTING)
        self.assertEqual(len(all_paths), 8)
        self.assertEqual(
            unpacked,
            {
                "/node_modules/better-sqlite3",
                "/node_modules/better-sqlite3/build/Release/better_sqlite3.node",
                "/node_modules/node-pty/build/Release/pty.node",
            },
        )

    def test_windows_separators_are_normalised(self):
        _, unpacked = win.parse_asar_listing("unpack : \\node_modules\\x\\y.node\r\n")
        self.assertEqual(unpacked, {"/node_modules/x/y.node"})

    def test_unexpected_lines_are_errors(self):
        with self.assertRaises(RuntimeError):
            win.parse_asar_listing("/node_modules/x\n")
        with self.assertRaises(RuntimeError):
            win.parse_asar_listing("maybe : /node_modules/x\n")

    def test_verify_unpacked_preserved(self):
        win.verify_unpacked_preserved(LISTING, LISTING + "pack   : /.vite/build/ui-test-bridge.cjs\n")
        repacked = LISTING.replace(
            "unpack : /node_modules/node-pty/build/Release/pty.node",
            "pack   : /node_modules/node-pty/build/Release/pty.node",
        )
        with self.assertRaises(RuntimeError) as caught:
            win.verify_unpacked_preserved(LISTING, repacked)
        self.assertIn("/node_modules/node-pty/build/Release/pty.node", str(caught.exception))


class PreludeTests(unittest.TestCase):
    def test_windows_prelude_is_exact(self):
        self.assertEqual(
            win.windows_desktop_profile_prelude(),
            'process.env.SKY_CUA_SERVICE_NATIVE_PIPE_PATH="\\\\\\\\.\\\\pipe\\\\codex-subscription-router-computer-use-"'
            "+globalThis.crypto.randomUUID();"
            "process.env.CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH=`1`;",
        )

    def test_prelude_is_a_javascript_string_for_the_pipe(self):
        # The prefix is a JSON (hence JavaScript) string literal and the
        # per-launch suffix is appended at run time, so no other local account
        # can pre-create the pipe under a name known in advance.
        prelude = win.windows_desktop_profile_prelude()
        expression = prelude.split("=", 1)[1].split(";", 1)[0]
        literal, plus, suffix = expression.partition("+")
        self.assertEqual(plus, "+")
        self.assertEqual(json.loads(literal), r"\\.\pipe\codex-subscription-router-computer-use-")
        self.assertEqual(json.loads(literal), win.COMPUTER_USE_PIPE_PREFIX)
        self.assertEqual(suffix, "globalThis.crypto.randomUUID()")

    def test_prelude_composes_with_isolate_desktop_profile(self):
        extracted = Path(tempfile.mkdtemp()) / "asar"
        build = extracted / ".vite" / "build"
        build.mkdir(parents=True)
        (build / "bootstrap-a.js").write_text(
            "Xe.app.setPath(`userData`,Qt({appDataPath:Xe.app.getPath(`appData`),"
            "buildFlavor:`prod`,env:process.env}));"
            "await Up.initialize();let{runMainAppStartup:Rm}=1;",
            encoding="utf-8",
        )
        (build / "x.js").write_text(
            "initializeUpdater(){return this.options.enableUpdater?"
            "(this.updaterInitialization??=this.initializeUpdaterOnce(),"
            "this.updaterInitialization):Promise.resolve()}",
            encoding="utf-8",
        )
        patch_app.isolate_desktop_profile(extracted, win.windows_desktop_profile_prelude())
        self.assertEqual(
            (build / "bootstrap-a.js").read_text(encoding="utf-8"),
            win.windows_desktop_profile_prelude()
            + "Xe.app.setPath(`userData`,Xe.app.getPath(`appData`)+`/Codex Subscription Router`);"
            "let{runMainAppStartup:Rm}=1;",
        )


EXE_INFO = {
    "path": "C:\\Apps\\ChatGPT\\ChatGPT.exe",
    "size": 1,
    "machine": "x64",
    "subsystem": 2,
    "signed": True,
    "versionInfo": {
        "fileVersion": "1.2026.100.0",
        "productVersion": "1.2026.100.0",
        "strings": {"ProductName": "ChatGPT", "CompanyName": "OpenAI"},
    },
    "asarIntegrity": [{"file": "resources\\app.asar", "alg": "SHA256", "value": "ab" * 32}],
}


class SourceIdentityTests(unittest.TestCase):
    def test_identity_from_exe_info(self):
        identity = win.source_identity(EXE_INFO, "cd" * 32)
        self.assertEqual(
            identity,
            win.SourceIdentity("1.2026.100.0", "1.2026.100.0", "ChatGPT", True, "cd" * 32),
        )

    def test_missing_version_info_falls_back_to_unknown(self):
        identity = win.source_identity({"signed": False, "versionInfo": None}, "cd" * 32)
        self.assertEqual(identity.product_version, "unknown")
        self.assertEqual(identity.file_version, "unknown")
        self.assertEqual(identity.product_name, "unknown")
        self.assertFalse(identity.signed)
        identity = win.source_identity({"versionInfo": {"strings": None}}, "x")
        self.assertEqual(identity.product_name, "unknown")


class ApproveSourceTests(unittest.TestCase):
    IDENTITY = win.SourceIdentity("1.0.0.0", "1.0.0.0", "ChatGPT", True, "ab" * 32)

    def test_empty_table_requires_the_flag(self):
        with self.assertRaises(RuntimeError) as caught:
            win.approve_source(self.IDENTITY, False)
        self.assertIn("--allow-untested-source", str(caught.exception))
        with mock.patch.object(win.sys, "stderr") as stderr:
            win.approve_source(self.IDENTITY, True)
        self.assertTrue(stderr.write.called)

    def test_matching_table_entry_passes_without_the_flag(self):
        table = {("1.0.0.0", "1.0.0.0"): "ab" * 32}
        with mock.patch.object(win, "TESTED_WINDOWS_SOURCE_BUILDS", table):
            win.approve_source(self.IDENTITY, False)
            with self.assertRaises(RuntimeError):
                win.approve_source(
                    win.SourceIdentity("1.0.0.0", "1.0.0.0", "ChatGPT", True, "cd" * 32),
                    False,
                )


class ProtocolSchemeTests(unittest.TestCase):
    def test_call_forms_are_retargeted_and_decoys_are_kept(self):
        extracted = Path(tempfile.mkdtemp()) / "asar"
        build = extracted / ".vite" / "build"
        build.mkdir(parents=True)
        (build / "main-a.js").write_text(
            "e.app.setAsDefaultProtocolClient(`codex`);"
            'if(!e.app.isDefaultProtocolClient("codex"))x();'
            "const kind=`codex`;const url=`codex://open`;"
            "e.app.removeAsDefaultProtocolClient('codex-legacy');",
            encoding="utf-8",
        )
        (build / "other.js").write_text("e.app.removeAsDefaultProtocolClient('codex')", encoding="utf-8")
        self.assertEqual(win.retarget_protocol_scheme(extracted), 3)
        self.assertEqual(
            (build / "main-a.js").read_text(encoding="utf-8"),
            "e.app.setAsDefaultProtocolClient(`codex-subscription-router`);"
            'if(!e.app.isDefaultProtocolClient("codex-subscription-router"))x();'
            "const kind=`codex`;const url=`codex://open`;"
            "e.app.removeAsDefaultProtocolClient('codex-legacy');",
        )
        self.assertEqual(
            (build / "other.js").read_text(encoding="utf-8"),
            "e.app.removeAsDefaultProtocolClient('codex-subscription-router')",
        )

    def test_zero_matches_is_reported_not_raised(self):
        extracted = Path(tempfile.mkdtemp()) / "asar"
        (extracted / ".vite" / "build").mkdir(parents=True)
        self.assertEqual(win.retarget_protocol_scheme(extracted), 0)

    def test_registration_with_a_non_literal_scheme_fails_closed(self):
        # A registration that cannot be retargeted would make the copy take
        # that scheme over through HKCU\Software\Classes at first launch, so
        # the patch stops and leaves the bundle unmodified.
        for residue in (
            "const s=`codex`;e.app.setAsDefaultProtocolClient(s);",
            "e.app.setAsDefaultProtocolClient('chatgpt');",
            "e.app.setAsDefaultProtocolClient.call(e.app,`codex`);",
            "e.app.setAsDefaultProtocolClient?.(`codex`);",
            "const f=e.app.setAsDefaultProtocolClient;f(`codex`);",
        ):
            with self.subTest(residue=residue):
                extracted = Path(tempfile.mkdtemp()) / "asar"
                build = extracted / ".vite" / "build"
                build.mkdir(parents=True)
                text = "e.app.setAsDefaultProtocolClient(`codex`);" + residue
                (build / "main-a.js").write_text(text, encoding="utf-8")
                with self.assertRaises(RuntimeError) as caught:
                    win.retarget_protocol_scheme(extracted)
                self.assertIn("main-a.js", str(caught.exception))
                self.assertIn("setAsDefaultProtocolClient", str(caught.exception))
                self.assertEqual((build / "main-a.js").read_text(encoding="utf-8"), text)


class LongPathTests(unittest.TestCase):
    def test_longest_path_length(self):
        root = Path(tempfile.mkdtemp()) / "app"
        deep = root / "a" / "bb" / "ccc.txt"
        deep.parent.mkdir(parents=True)
        deep.write_bytes(b"")
        self.assertEqual(win.longest_path_length(root), len(str(deep)))
        self.assertEqual(win.longest_path_length(root / "a" / "bb"), len(str(deep)))

    def test_boundary(self):
        source = Path("/s")
        destination = Path("/d")
        # longest + len(dest) - len(source) + 8 == 260 exactly -> requires support.
        longest = 260 - 8
        win.require_long_path_support(source, destination, longest - 1, None)
        with self.assertRaises(RuntimeError) as caught:
            win.require_long_path_support(source, destination, longest, None)
        self.assertIn("LongPathsEnabled", str(caught.exception))
        self.assertIn("HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem", str(caught.exception))
        with self.assertRaises(RuntimeError):
            win.require_long_path_support(source, destination, longest, False)
        win.require_long_path_support(source, destination, longest, True)
        # A longer destination shifts the projection.
        with self.assertRaises(RuntimeError):
            win.require_long_path_support(source, Path("/destination"), longest - 5, None)

    def test_staging_shape_is_longer_than_the_destination(self):
        destination = Path("/lad/Programs/Codex Subscription Router")
        shape = win.staging_shape(destination)
        self.assertEqual(shape.parent.parent, destination.parent)
        self.assertEqual(shape.name, "Codex Subscription Router")
        self.assertEqual(len(str(shape)), len(str(destination)) + len(".codex-subscription-router-") + 8 + 1)


class CommandCompositionTests(unittest.TestCase):
    def test_launcher_ldflags(self):
        self.assertEqual(
            win.launcher_ldflags("ChatGPT.exe"),
            "-s -w -H=windowsgui -X main.electronExecutable=ChatGPT.exe",
        )
        for bad in ("Chat GPT.exe", 'a"b.exe', "a'b.exe", "a`b.exe", "", "a\tb.exe"):
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                win.launcher_ldflags(bad)

    def test_asar_command_uses_the_esm_entry_point(self):
        with mock.patch.object(patch_app, "ensure_asar_tool") as ensure, \
             mock.patch.object(win.shutil, "which", return_value="/usr/bin/node"), \
             mock.patch.object(win.ASAR_CLI.__class__, "is_file", return_value=True):
            command = win.asar_command()
        ensure.assert_called_once_with()
        self.assertEqual(command[0], "/usr/bin/node")
        self.assertTrue(command[1].endswith("asar.mjs"))
        self.assertFalse(any(part.lower().endswith(".cmd") for part in command))
        self.assertNotIn(".bin", command[1])

    def test_asar_command_fails_without_node_or_cli(self):
        with mock.patch.object(patch_app, "ensure_asar_tool"), \
             mock.patch.object(win.shutil, "which", return_value=None), \
             mock.patch.object(win.ASAR_CLI.__class__, "is_file", return_value=True), \
             self.assertRaises(RuntimeError):
            win.asar_command()
        with mock.patch.object(patch_app, "ensure_asar_tool"), \
             mock.patch.object(win.ASAR_CLI.__class__, "is_file", return_value=False), \
             self.assertRaises(RuntimeError):
            win.asar_command()

    def test_exe_info_runs_the_helper_and_parses_json(self):
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(EXE_INFO), stderr="")
        with mock.patch.object(win.subprocess, "run", return_value=completed) as run, \
             mock.patch.object(win.shutil, "which", return_value="/usr/bin/node"), \
             mock.patch.object(win.EXE_INFO_SCRIPT.__class__, "is_file", return_value=True):
            info = win.exe_info(Path("/apps/ChatGPT/ChatGPT.exe"))
        self.assertEqual(info, EXE_INFO)
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/node")
        self.assertTrue(command[1].endswith("exe-info.mjs"))
        self.assertEqual(command[2], str(Path("/apps/ChatGPT/ChatGPT.exe")))
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_helper_failures_carry_the_helper_message(self):
        # CalledProcessError would report only the exit status; the reason is
        # on stderr and must reach the operator so the failed check is named.
        failures = (
            (
                lambda: win.exe_info(Path("/apps/ChatGPT/ChatGPT.exe")),
                win.EXE_INFO_SCRIPT,
                subprocess.CompletedProcess(
                    [], 2, stdout="", stderr="exe-info: X is not a PE executable (no MZ header)\n"
                ),
                "exe-info failed (exit 2): exe-info: X is not a PE executable (no MZ header)",
            ),
            (
                lambda: win.rewrite_asar_integrity(
                    Path("/stage/ChatGPT.exe"), EXE_INFO["asarIntegrity"][0], "cd" * 32
                ),
                win.SET_ASAR_INTEGRITY_SCRIPT,
                subprocess.CompletedProcess(
                    [], 1, stdout="", stderr="set-asar-integrity: verification failed: "
                    "ChatGPT.exe lost its integrity resource\n"
                ),
                "set-asar-integrity failed (exit 1): set-asar-integrity: verification failed",
            ),
            (
                lambda: win.ensure_destination_processes_stopped(Path("/lad/Programs/Router")),
                win.EXE_INFO_SCRIPT,
                subprocess.CompletedProcess(
                    [], 1, stdout="", stderr="Get-CimInstance : Access denied\n"
                ),
                "process check (Get-CimInstance) failed (exit 1): Get-CimInstance : Access denied",
            ),
            (
                lambda: win.exe_info(Path("/apps/ChatGPT/ChatGPT.exe")),
                win.EXE_INFO_SCRIPT,
                subprocess.CompletedProcess([], 3, stdout="on stdout only", stderr=""),
                "exe-info failed (exit 3): on stdout only",
            ),
            (
                lambda: win.exe_info(Path("/apps/ChatGPT/ChatGPT.exe")),
                win.EXE_INFO_SCRIPT,
                subprocess.CompletedProcess([], 4, stdout="", stderr=""),
                "exe-info failed (exit 4): no output",
            ),
        )
        for call, script, completed, expected in failures:
            with self.subTest(expected=expected), \
                 mock.patch.object(win.subprocess, "run", return_value=completed) as run, \
                 mock.patch.object(win.shutil, "which", return_value="/usr/bin/node"), \
                 mock.patch.object(script.__class__, "is_file", return_value=True), \
                 self.assertRaises(RuntimeError) as caught:
                call()
            self.assertIn(expected, str(caught.exception))
            self.assertFalse(run.call_args.kwargs["check"])

    def test_helper_output_that_is_not_json_is_a_patch_failure(self):
        completed = subprocess.CompletedProcess([], 0, stdout="not json", stderr="")
        with mock.patch.object(win.subprocess, "run", return_value=completed), \
             mock.patch.object(win.shutil, "which", return_value="/usr/bin/node"), \
             mock.patch.object(win.EXE_INFO_SCRIPT.__class__, "is_file", return_value=True), \
             self.assertRaises(RuntimeError) as caught:
            win.exe_info(Path("/apps/ChatGPT/ChatGPT.exe"))
        self.assertIn("exe-info did not print JSON", str(caught.exception))

    def test_asar_output_decodes_utf8(self):
        # asar writes UTF-8 to the pipe; Python's default text mode would use
        # the ANSI code page on Windows.
        with mock.patch.object(
            win.subprocess, "check_output", return_value="pack   : /a/\u00e9.js\n"
        ) as check_output:
            listing = win.asar_output(["node", "asar.mjs", "list", "--is-pack", "x.asar"])
        self.assertEqual(listing, "pack   : /a/\u00e9.js")
        self.assertEqual(check_output.call_args.args[0], ["node", "asar.mjs", "list", "--is-pack", "x.asar"])
        self.assertEqual(check_output.call_args.kwargs["encoding"], "utf-8")
        self.assertTrue(check_output.call_args.kwargs["text"])

    def test_integrity_entry_normalises_slashes_and_case(self):
        info = {"asarIntegrity": [{"file": "Resources/App.asar", "alg": "SHA256", "value": "x"}]}
        self.assertEqual(win.integrity_entry_for(info, "resources\\app.asar")["value"], "x")
        self.assertIsNone(win.integrity_entry_for(info, "resources\\other.asar"))
        self.assertIsNone(win.integrity_entry_for({"asarIntegrity": None}, "resources\\app.asar"))
        self.assertIsNone(win.integrity_entry_for({}, "resources\\app.asar"))
        duplicated = {"asarIntegrity": info["asarIntegrity"] * 2}
        with self.assertRaises(RuntimeError):
            win.integrity_entry_for(duplicated, "resources\\app.asar")

    def test_rewrite_asar_integrity_verifies_the_result(self):
        new_list = [{"file": "resources\\app.asar", "alg": "SHA256", "value": "cd" * 32}]
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(new_list), stderr="")
        entry = EXE_INFO["asarIntegrity"][0]
        with mock.patch.object(win.subprocess, "run", return_value=completed) as run, \
             mock.patch.object(win.shutil, "which", return_value="/usr/bin/node"), \
             mock.patch.object(win.SET_ASAR_INTEGRITY_SCRIPT.__class__, "is_file", return_value=True):
            recorded = win.rewrite_asar_integrity(Path("/stage/ChatGPT.exe"), entry, "cd" * 32)
            command = run.call_args.args[0]
            self.assertTrue(command[1].endswith("set-asar-integrity.mjs"))
            self.assertEqual(command[2:], [str(Path("/stage/ChatGPT.exe")), "resources\\app.asar", "cd" * 32])
            with self.assertRaises(RuntimeError):
                win.rewrite_asar_integrity(Path("/stage/ChatGPT.exe"), entry, "ef" * 32)
            with self.assertRaises(RuntimeError):
                win.rewrite_asar_integrity(Path("/stage/ChatGPT.exe"), {**entry, "alg": "MD5"}, "cd" * 32)
        self.assertEqual(recorded, "cd" * 32)

    def test_stopped_processes_command(self):
        command = win.powershell_stopped_processes_command(Path("C:\\Apps\\O'Neil\\Router"))
        self.assertEqual(command[:4], ["powershell", "-NoProfile", "-NonInteractive", "-Command"])
        script = command[4]
        self.assertIn("Get-CimInstance Win32_Process", script)
        self.assertIn("'C:\\Apps\\O''Neil\\Router\\'", script)
        self.assertIn("OrdinalIgnoreCase", script)
        self.assertIn("ProcessId", script)

    def test_icacls_commands(self):
        # /reset first: /inheritance:r and /grant:r leave explicit ACEs that
        # other principals already hold on an existing root in place.
        self.assertEqual(
            win.icacls_commands(Path("C:\\Users\\me\\.codex-mux"), "PC\\me"),
            [
                ["icacls", "C:\\Users\\me\\.codex-mux", "/reset"],
                [
                    "icacls", "C:\\Users\\me\\.codex-mux", "/inheritance:r", "/grant:r",
                    "PC\\me:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F",
                ],
            ],
        )

    def test_harden_state_root_runs_both_commands_in_order(self):
        with mock.patch.object(patch_app, "run") as run, \
             mock.patch.dict(win.os.environ, {"USERNAME": "me", "USERDOMAIN": "PC"}):
            win.harden_state_root(Path("C:\\Users\\me\\.codex-mux"))
        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            win.icacls_commands(Path("C:\\Users\\me\\.codex-mux"), "PC\\me"),
        )

    def test_shortcut_command(self):
        launcher = Path("/lad/Programs/Codex Subscription Router/Codex Subscription Router.exe")
        command = win.shortcut_command(launcher, Path("/appdata/Start Menu/Programs/It's.lnk"))
        script = command[4]
        self.assertIn("WScript.Shell", script)
        self.assertIn(f"CreateShortcut('{Path('/appdata/Start Menu/Programs/It' + chr(39) * 2 + 's.lnk')}')", script)
        self.assertIn(f"TargetPath = '{launcher}'", script)
        self.assertIn(f"WorkingDirectory = '{launcher.parent}'", script)
        self.assertIn("Save()", script)

    def test_current_username_prefers_domain(self):
        self.assertEqual(win.current_username({"USERNAME": "me", "USERDOMAIN": "PC"}), "PC\\me")
        self.assertEqual(win.current_username({"USERNAME": "me"}), "me")

    def test_start_menu_shortcut_path(self):
        self.assertEqual(
            win.start_menu_shortcut_path({"APPDATA": "/appdata"}),
            Path("/appdata/Microsoft/Windows/Start Menu/Programs/Codex Subscription Router.lnk"),
        )


class ArgumentTests(unittest.TestCase):
    def test_defaults(self):
        args = win.parse_args([])
        self.assertIsNone(args.source)
        self.assertIsNone(args.destination)
        self.assertFalse(args.force)
        self.assertFalse(args.allow_untested_source)
        self.assertIsNone(args.electron_executable)
        self.assertIsNone(args.codex_executable)
        self.assertFalse(args.no_shortcut)

    def test_main_reports_the_windows_guard_as_a_patch_failure(self):
        if sys.platform == "win32":
            self.skipTest("guard does not fire on Windows")
        with mock.patch.object(win.sys, "stderr") as stderr:
            self.assertEqual(win.main([]), 1)
        self.assertIn("patch failed", "".join(str(call.args[0]) for call in stderr.write.call_args_list))


class HelperJsonTests(unittest.TestCase):
    def test_helper_json_returns_whatever_json_value_the_helper_printed(self):
        # exe-info prints an object, set-asar-integrity a list; the shape check
        # is each caller's job, helper_json only guarantees valid JSON.
        self.assertEqual(win.helper_json(subprocess.CompletedProcess([], 0, stdout="[1]"), "t"), [1])
        self.assertEqual(win.helper_json(subprocess.CompletedProcess([], 0, stdout='{"a":1}'), "t"), {"a": 1})
        with self.assertRaises(RuntimeError) as caught:
            win.helper_json(subprocess.CompletedProcess([], 0, stdout=""), "probe")
        self.assertIn("probe did not print JSON", str(caught.exception))

    def test_exe_info_that_is_valid_json_but_not_an_object_is_a_patch_failure(self):
        for stdout in ("[]", '"ChatGPT.exe"', "null", "1", '[{"versionInfo": {}}]'):
            completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
            with self.subTest(stdout=stdout), \
                 mock.patch.object(win.subprocess, "run", return_value=completed), \
                 mock.patch.object(win.shutil, "which", return_value="/usr/bin/node"), \
                 mock.patch.object(win.EXE_INFO_SCRIPT.__class__, "is_file", return_value=True), \
                 self.assertRaises(RuntimeError) as caught:
                win.exe_info(Path("/apps/ChatGPT/ChatGPT.exe"))
            self.assertIn("exe-info did not return an object", str(caught.exception))

    def test_set_asar_integrity_that_is_not_a_list_does_not_count_as_recorded(self):
        entry = EXE_INFO["asarIntegrity"][0]
        for stdout in ("{}", '"cd"', "null"):
            completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
            with self.subTest(stdout=stdout), \
                 mock.patch.object(win.subprocess, "run", return_value=completed), \
                 mock.patch.object(win.shutil, "which", return_value="/usr/bin/node"), \
                 mock.patch.object(win.SET_ASAR_INTEGRITY_SCRIPT.__class__, "is_file", return_value=True), \
                 self.assertRaises(RuntimeError) as caught:
                win.rewrite_asar_integrity(Path("/stage/ChatGPT.exe"), entry, "cd" * 32)
            self.assertIn("did not record the new asar digest", str(caught.exception))


class HelperDecodingTests(unittest.TestCase):
    """run_helper decodes UTF-8; only the PowerShell caller tolerates other bytes."""

    def test_run_helper_decodes_strictly_by_default(self):
        completed = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        with mock.patch.object(win.subprocess, "run", return_value=completed) as run:
            win.run_helper(["node", "x.mjs"], "probe")
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.kwargs["errors"], "strict")
        self.assertFalse(run.call_args.kwargs["check"])

    def test_node_helpers_keep_the_strict_decoder(self):
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(EXE_INFO), stderr="")
        with mock.patch.object(win.subprocess, "run", return_value=completed) as run, \
             mock.patch.object(win.shutil, "which", return_value="/usr/bin/node"), \
             mock.patch.object(win.EXE_INFO_SCRIPT.__class__, "is_file", return_value=True):
            win.exe_info(Path("/apps/ChatGPT/ChatGPT.exe"))
        self.assertEqual(run.call_args.kwargs["errors"], "strict")

    def test_localized_powershell_failure_reaches_the_operator(self):
        # Windows PowerShell 5.1 writes the OEM code page, so the decoder must
        # be lenient for this caller; the text still has to be in the error.
        completed = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="Get-CimInstance : Accès refusé\r\n"
        )
        with mock.patch.object(win.subprocess, "run", return_value=completed) as run, \
             self.assertRaises(RuntimeError) as caught:
            win.ensure_destination_processes_stopped(Path("/lad/Programs/Router"))
        self.assertIn("Accès refusé", str(caught.exception))
        self.assertIn("process check (Get-CimInstance) failed (exit 1)", str(caught.exception))
        self.assertEqual(run.call_args.kwargs["errors"], "replace")
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertEqual(run.call_args.args[0][0], "powershell")

    def test_running_processes_are_named_with_the_destination(self):
        completed = subprocess.CompletedProcess([], 0, stdout="1234\r\n5678\r\n", stderr="")
        destination = Path("/lad/Programs/Router")
        with mock.patch.object(win.subprocess, "run", return_value=completed), \
             self.assertRaises(RuntimeError) as caught:
            win.ensure_destination_processes_stopped(destination)
        message = str(caught.exception)
        self.assertIn("quit the running app", message)
        self.assertIn("1234, 5678", message)
        self.assertIn(str(destination), message)

    def test_no_running_processes_returns_normally(self):
        for stdout in ("", "\r\n", "\n\n"):
            completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")
            with self.subTest(stdout=stdout), \
                 mock.patch.object(win.subprocess, "run", return_value=completed):
                self.assertIsNone(win.ensure_destination_processes_stopped(Path("/lad/Programs/Router")))

    def test_undecodable_bytes_are_replaced_not_fatal(self):
        # A real child and a real pipe: these are the bytes code page 850 or
        # 1252 emits for "Accès refusé", and they are not valid UTF-8. With
        # errors="replace" the failure is still a RuntimeError main() reports;
        # the strict default would surface UnicodeDecodeError instead.
        program = "import sys; sys.stderr.buffer.write(b'Acc\\xe8s refus\\xe9'); sys.exit(1)"
        command = [sys.executable, "-c", program]
        with self.assertRaises(RuntimeError) as caught:
            win.run_helper(command, "probe", errors="replace")
        self.assertIn("probe failed (exit 1): Acc�s refus�", str(caught.exception))
        # The strict default is still an error, but a RuntimeError main() can
        # report, whichever way subprocess surfaces the decode failure: POSIX
        # raises UnicodeDecodeError from run(), Windows returns None streams.
        with self.assertRaises(RuntimeError) as caught:
            win.run_helper(command, "probe")
        self.assertIn("probe wrote output that is not UTF-8", str(caught.exception))

    def test_none_streams_from_a_dead_reader_thread_are_an_error(self):
        # Windows: communicate() decodes in reader threads, and a strict decode
        # failure kills the thread and leaves that stream None (seen in CI as
        # AttributeError: 'NoneType' object has no attribute 'strip').
        for stdout, stderr in ((None, ""), ("", None), (None, None)):
            completed = subprocess.CompletedProcess([], 0, stdout=stdout, stderr=stderr)
            with self.subTest(stdout=stdout, stderr=stderr), \
                 mock.patch.object(win.subprocess, "run", return_value=completed), \
                 self.assertRaises(RuntimeError) as caught:
                win.run_helper(["probe"], "probe")
            self.assertIn("probe wrote output that is not UTF-8", str(caught.exception))


class NotSameDevice(OSError):
    """OSError as Windows raises it for a cross-volume rename.

    On Windows the interpreter sets .winerror itself; POSIX hosts have no such
    attribute, so the class supplies it and the errno is deliberately not
    EXDEV to prove that the winerror alone selects the fallback.
    """

    winerror = win.ERROR_NOT_SAME_DEVICE


class SwapIntoPlaceTests(unittest.TestCase):
    """One backup move, one atomic rename, and a handler that only restores."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        programs = self.root / "Local" / "Programs"
        self.destination = programs / "Codex Subscription Router"
        self.stage = programs / ".codex-subscription-router-xxxxxxxx" / "Codex Subscription Router"
        self.backup_directory = self.root / ".codex-mux" / "backups" / "20260101-000000"
        self.app_backup = self.backup_directory / self.destination.name
        make_app(self.stage, "ChatGPT.exe", "Codex Subscription Router.exe")
        (self.stage / "marker").write_text("new", encoding="utf-8")

    def install_previous_app(self):
        make_app(self.destination, "ChatGPT.exe")
        (self.destination / "marker").write_text("old", encoding="utf-8")
        # patch_app creates the timestamped backup directory before the swap.
        self.backup_directory.mkdir(parents=True)

    @staticmethod
    def failing_renames(failures: dict):
        """Patch Path.rename to raise the given error for the given paths."""
        real_rename = Path.rename

        def rename(path, target):
            error = failures.get(path)
            if error is not None:
                raise error
            return real_rename(path, target)

        return mock.patch.object(win.Path, "rename", rename)

    def swap(self, had_app=True):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            win.swap_into_place(self.stage, self.destination, self.backup_directory, had_app)
        return out.getvalue()

    def test_happy_path_moves_the_backup_then_the_stage(self):
        self.install_previous_app()
        out = self.swap()
        self.assertEqual((self.destination / "marker").read_text(encoding="utf-8"), "new")
        self.assertTrue((self.destination / "Codex Subscription Router.exe").is_file())
        self.assertEqual((self.app_backup / "marker").read_text(encoding="utf-8"), "old")
        self.assertFalse(self.stage.exists())
        self.assertFalse((self.backup_directory / "failed-install").exists())
        self.assertIn(f"Existing copy moved to {self.app_backup}", out)

    def test_first_install_has_nothing_to_back_up(self):
        out = self.swap(had_app=False)
        self.assertEqual((self.destination / "marker").read_text(encoding="utf-8"), "new")
        self.assertFalse(self.stage.exists())
        self.assertFalse(self.backup_directory.exists())
        self.assertNotIn("Existing copy", out)

    def test_failed_backup_move_leaves_the_previous_install_untouched(self):
        # The macOS-derived handler moved the working install into
        # failed-install here because destination.exists() was true.
        self.install_previous_app()
        locked = PermissionError(
            errno.EACCES, "The process cannot access the file because it is being used by another process"
        )
        with self.failing_renames({self.destination: locked}), \
             mock.patch.object(win.shutil, "move") as move, \
             self.assertRaises(PermissionError) as caught:
            self.swap()
        self.assertIs(caught.exception, locked)
        move.assert_not_called()
        self.assertEqual((self.destination / "marker").read_text(encoding="utf-8"), "old")
        self.assertTrue((self.destination / "ChatGPT.exe").is_file())
        self.assertEqual(list(self.backup_directory.iterdir()), [])
        self.assertFalse((self.backup_directory / "failed-install").exists())
        # The staged copy is left for the temporary directory to discard.
        self.assertEqual((self.stage / "marker").read_text(encoding="utf-8"), "new")

    def test_failed_stage_rename_restores_the_backup(self):
        self.install_previous_app()
        locked = PermissionError(errno.EACCES, "Access is denied")
        with self.failing_renames({self.stage: locked}), \
             self.assertRaises(PermissionError) as caught:
            self.swap()
        self.assertIs(caught.exception, locked)
        self.assertEqual((self.destination / "marker").read_text(encoding="utf-8"), "old")
        self.assertFalse(self.app_backup.exists())
        self.assertFalse((self.backup_directory / "failed-install").exists())
        self.assertEqual((self.stage / "marker").read_text(encoding="utf-8"), "new")

    def test_failed_first_install_leaves_no_destination_and_no_backup(self):
        locked = PermissionError(errno.EACCES, "Access is denied")
        with self.failing_renames({self.stage: locked}), self.assertRaises(PermissionError):
            self.swap(had_app=False)
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.backup_directory.exists())
        self.assertEqual((self.stage / "marker").read_text(encoding="utf-8"), "new")

    def test_backup_move_falls_back_to_shutil_move_across_volumes(self):
        for error in (
            OSError(errno.EXDEV, "Invalid cross-device link"),
            NotSameDevice(errno.EINVAL, "The system cannot move the file to a different disk drive"),
        ):
            with self.subTest(error=type(error).__name__):
                self.setUp()
                self.install_previous_app()
                with self.failing_renames({self.destination: error}), \
                     mock.patch.object(win.shutil, "move", wraps=shutil.move) as move:
                    self.swap()
                move.assert_called_once_with(self.destination, self.app_backup)
                self.assertEqual((self.destination / "marker").read_text(encoding="utf-8"), "new")
                self.assertEqual((self.app_backup / "marker").read_text(encoding="utf-8"), "old")
                self.assertFalse(self.stage.exists())

    def test_cross_volume_backup_is_restored_the_same_way(self):
        # Backup went to another volume, then the stage rename failed: the
        # restore has to cross that volume too, so it must not be a bare rename.
        self.install_previous_app()
        cross = OSError(errno.EXDEV, "Invalid cross-device link")
        locked = PermissionError(errno.EACCES, "Access is denied")
        with self.failing_renames({self.destination: cross, self.app_backup: cross, self.stage: locked}), \
             mock.patch.object(win.shutil, "move", wraps=shutil.move) as move, \
             self.assertRaises(PermissionError) as caught:
            self.swap()
        self.assertIs(caught.exception, locked)
        self.assertEqual(
            [call.args for call in move.call_args_list],
            [(self.destination, self.app_backup), (self.app_backup, self.destination)],
        )
        self.assertEqual((self.destination / "marker").read_text(encoding="utf-8"), "old")
        self.assertFalse(self.app_backup.exists())

    def test_move_directory_only_falls_back_for_cross_device_errors(self):
        source = self.root / "a"
        source.mkdir()
        for error in (
            PermissionError(errno.EACCES, "denied"),
            FileNotFoundError(errno.ENOENT, "gone"),
            OSError(errno.EBUSY, "busy"),
        ):
            with self.subTest(error=error), \
                 self.failing_renames({source: error}), \
                 mock.patch.object(win.shutil, "move") as move, \
                 self.assertRaises(OSError) as caught:
                win.move_directory(source, self.root / "b")
            self.assertIs(caught.exception, error)
            move.assert_not_called()
        self.assertTrue(source.is_dir())


class SourceSideLayoutTests(unittest.TestCase):
    """The layout checks patch_app runs on the source hold for the staged copy."""

    def test_layout_checks_give_the_same_relative_results_on_a_copy(self):
        root = Path(tempfile.mkdtemp())
        source = make_app(
            root / "Local" / "Programs" / "ChatGPT",
            "ChatGPT.exe",
            unpacked={"@openai": {}, "better-sqlite3": {}, "node-pty": {}},
        )
        nested = source / "resources" / "app.asar.unpacked" / "node_modules" / "@openai" / "codex" / "bin"
        nested.mkdir(parents=True)
        (nested / "codex.exe").write_bytes(b"MZ codex")
        copy = root / "Local" / "Programs" / ".codex-subscription-router-xxxxxxxx" / "Codex Subscription Router"
        shutil.copytree(source, copy, symlinks=False)

        self.assertEqual(win.unpack_globs(source), "node_modules/{@openai,better-sqlite3,node-pty}")
        self.assertEqual(win.unpack_globs(source), win.unpack_globs(copy))
        for override in (None, "resources/app.asar.unpacked/node_modules/@openai/codex/bin/codex.exe"):
            with self.subTest(override=override):
                source_codex = win.locate_codex_executable(source, override)
                copy_codex = win.locate_codex_executable(copy, override)
                self.assertEqual(source_codex.relative_to(source), copy_codex.relative_to(copy))
                # The derivation patch_app uses instead of searching the stage.
                self.assertEqual(copy / source_codex.relative_to(source), copy_codex)
                self.assertTrue(copy_codex.is_file())
        self.assertFalse(win.locate_codex_executable(source, None).with_name("codex.real.exe").exists())


# --- hermetic orchestration of patch_app() -----------------------------------

BOOTSTRAP_BUNDLE = (
    "Xe.app.setPath(`userData`,Qt({appDataPath:Xe.app.getPath(`appData`),"
    "buildFlavor:`prod`,env:process.env}));"
    "await Up.initialize();let{runMainAppStartup:Rm}=1;"
)
UPDATER_BUNDLE = (
    "initializeUpdater(){return this.options.enableUpdater?"
    "(this.updaterInitialization??=this.initializeUpdaterOnce(),"
    "this.updaterInitialization):Promise.resolve()}"
)
MAIN_BUNDLE = "e.app.setAsDefaultProtocolClient(`codex`);"
REPACKED_HEADER = json.dumps({"files": {".vite": {"files": {}}}}).encode("utf-8")
# asar_header_digest reads the fourth little-endian uint32 as the header length
# and hashes that many following bytes; the other fields are pickle sizes.
REPACKED_ASAR = (
    struct.pack("<IIII", 4, len(REPACKED_HEADER) + 8, len(REPACKED_HEADER) + 4, len(REPACKED_HEADER))
    + REPACKED_HEADER
)


class FakeTools:
    """Stand-ins for go, node/asar, icacls and PowerShell that record every call.

    Nothing here spawns a process or leaves the temporary tree: extract writes
    the three bundles the shared patch steps anchor on, pack snapshots the
    patched bundles and writes a minimal but valid asar, and the Go builds
    write placeholder executables.
    """

    def __init__(self):
        self.events: list = []
        self.run_commands: list = []
        self.subprocess_runs: list = []
        self.builds: list = []
        self.packed_bundles: dict = {}
        self.unpack_dir = None

    def record(self, name, function):
        def wrapper(*args, **kwargs):
            self.events.append((name, args[0] if args else None))
            return function(*args, **kwargs)

        return wrapper

    def asar_command(self):
        self.events.append(("asar_command", None))
        return ["node", "asar.mjs"]

    def asar_output(self, command):
        self.run_commands.append(command)
        return LISTING

    def run(self, command, *, cwd=None, env=None):
        self.run_commands.append(command)
        if command[:3] == ["node", "asar.mjs", "extract"]:
            build = Path(command[4]) / ".vite" / "build"
            build.mkdir(parents=True)
            (build / "bootstrap-a.js").write_text(BOOTSTRAP_BUNDLE, encoding="utf-8")
            (build / "x.js").write_text(UPDATER_BUNDLE, encoding="utf-8")
            (build / "main-a.js").write_text(MAIN_BUNDLE, encoding="utf-8")
        elif command[:3] == ["node", "asar.mjs", "pack"]:
            extracted, repacked = Path(command[-2]), Path(command[-1])
            self.packed_bundles = {
                entry.name: entry.read_text(encoding="utf-8")
                for entry in (extracted / ".vite" / "build").iterdir()
            }
            if command[3] == "--unpack-dir":
                self.unpack_dir = command[4]
                native = repacked.parent / "app.asar.unpacked" / "node_modules" / "node-pty"
                native.mkdir(parents=True)
                (native / "pty.node").write_bytes(b"repacked native")
            repacked.write_bytes(REPACKED_ASAR)
        elif command[0] not in {"icacls", "powershell"}:
            raise AssertionError(f"unexpected command: {command}")

    def build_go_program(self, destination, package_path, *, goos=None, goarch=None, ldflags="-s -w"):
        self.events.append(("build", package_path))
        self.builds.append((destination, package_path, goos, goarch, ldflags))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"MZ built " + package_path.encode("utf-8"))

    def subprocess_run(self, command, **kwargs):
        self.subprocess_runs.append((command, kwargs))
        if command[0] == "powershell":
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        if command[1].endswith("exe-info.mjs"):
            self.events.append(("exe-info", Path(command[2])))
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps(EXE_INFO), stderr="")
        if command[1].endswith("set-asar-integrity.mjs"):
            entry = {"file": command[3], "alg": "SHA256", "value": command[4]}
            return subprocess.CompletedProcess(command, 0, stdout=json.dumps([entry]), stderr="")
        raise AssertionError(f"unexpected helper: {command}")


def snapshot(root: Path) -> dict:
    return {
        str(entry.relative_to(root)): entry.read_bytes() if entry.is_file() else None
        for entry in sorted(root.rglob("*"))
    }


class PatchAppOrchestrationTests(unittest.TestCase):
    """patch_app() end to end with every external effect stubbed.

    HOME/USERPROFILE/LOCALAPPDATA/APPDATA and the state root point into one
    temporary tree; go, node, icacls, PowerShell and the registry are never
    reached, and the source tree must be byte-identical afterwards.
    """

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.home = self.root / "home"
        self.local = self.root / "Local"
        self.appdata = self.root / "Roaming"
        self.state_root = self.home / ".codex-mux"
        for directory in (self.home, self.local, self.appdata):
            directory.mkdir()
        self.source = make_app(
            self.local / "Programs" / "ChatGPT",
            "ChatGPT.exe",
            "Uninstall ChatGPT.exe",
            unpacked={"@openai": {}, "node-pty": {}},
        )
        (self.source / "resources" / "app.asar").write_bytes(b"official asar")
        self.codex_relative = Path("resources") / "app.asar.unpacked" / "node_modules" / "@openai" / "codex" / "bin" / "codex.exe"
        (self.source / self.codex_relative).parent.mkdir(parents=True)
        (self.source / self.codex_relative).write_bytes(b"MZ official codex")
        self.destination = self.local / "Programs" / "Codex Subscription Router"
        self.environment = {
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "LOCALAPPDATA": str(self.local),
            "APPDATA": str(self.appdata),
            "ProgramFiles": str(self.root / "Program Files"),
            "USERNAME": "me",
            "USERDOMAIN": "PC",
        }

    def run_patch(self, *, force=False, create_shortcut=True, source=None, codex_executable=None):
        tools = FakeTools()
        before = snapshot(self.source)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, self.environment), \
             mock.patch.object(patch_app, "DEFAULT_STATE_ROOT", self.state_root), \
             mock.patch.object(patch_app, "run", tools.run), \
             mock.patch.object(patch_app, "build_go_program", tools.build_go_program), \
             mock.patch.object(patch_app, "require_tool", tools.record("require_tool", lambda name: None)), \
             mock.patch.object(patch_app, "patch_renderer") as patch_renderer, \
             mock.patch.object(win, "asar_command", tools.asar_command), \
             mock.patch.object(win, "asar_output", tools.asar_output), \
             mock.patch.object(win, "node_executable", lambda: "node"), \
             mock.patch.object(win, "long_paths_enabled", tools.record("long_paths_enabled", lambda: True)), \
             mock.patch.object(win, "unpack_globs", tools.record("unpack_globs", win.unpack_globs)), \
             mock.patch.object(
                 win, "locate_codex_executable",
                 tools.record("locate_codex_executable", win.locate_codex_executable),
             ), \
             mock.patch.object(win.subprocess, "run", tools.subprocess_run), \
             mock.patch.object(win.sys, "platform", "win32"), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            win.patch_app(source, None, force, True, None, codex_executable, create_shortcut)
        self.assertEqual(snapshot(self.source), before, "the official install was modified")
        tools.patch_renderer = patch_renderer
        tools.stdout, tools.stderr = out.getvalue(), err.getvalue()
        return tools

    def assert_installed(self, tools):
        destination = self.destination
        self.assertTrue(destination.is_dir())
        self.assertEqual((destination / "Codex Subscription Router.exe").read_bytes(), b"MZ built ./cmd/codex-router-launcher")
        self.assertEqual((destination / "ChatGPT.exe").read_bytes(), b"MZ")
        self.assertEqual((destination / "resources" / "app.asar").read_bytes(), REPACKED_ASAR)
        self.assertEqual(
            (destination / "resources" / "app.asar.unpacked" / "node_modules" / "node-pty" / "pty.node").read_bytes(),
            b"repacked native",
        )
        self.assertEqual((destination / self.codex_relative).read_bytes(), b"MZ built ./cmd/codex-mux")
        self.assertEqual(
            (destination / self.codex_relative).with_name("codex.real.exe").read_bytes(), b"MZ official codex"
        )
        # No staging directory is left beside the destination.
        self.assertEqual(
            sorted(entry.name for entry in destination.parent.iterdir()),
            ["ChatGPT", "Codex Subscription Router"],
        )
        # State root: the token, hardened through icacls, nothing else unexpected.
        self.assertRegex((self.state_root / "control-token").read_text(encoding="utf-8"), r"^[0-9a-f]{64}$")
        self.assertEqual(
            tools.run_commands[:2], win.icacls_commands(self.state_root, "PC\\me")
        )
        # Builds: the mux into the temporary directory, the launcher into the stage.
        (mux_path, mux_package, mux_goos, _, _), (launcher_path, launcher_package, launcher_goos, _, launcher_ldflags) = tools.builds
        self.assertEqual((mux_package, mux_goos), ("./cmd/codex-mux", "windows"))
        self.assertEqual(mux_path.name, "codex.exe")
        self.assertTrue(mux_path.parent.name.startswith(win.STAGING_PREFIX))
        self.assertEqual((launcher_package, launcher_goos), ("./cmd/codex-router-launcher", "windows"))
        self.assertEqual(launcher_path.name, "Codex Subscription Router.exe")
        self.assertEqual(launcher_ldflags, win.launcher_ldflags("ChatGPT.exe"))
        # The shared patch steps ran on the extracted bundles before packing.
        bootstrap = tools.packed_bundles["bootstrap-a.js"]
        self.assertTrue(bootstrap.startswith(win.windows_desktop_profile_prelude()))
        self.assertNotIn("Up.initialize()", bootstrap)
        self.assertIn("setAsDefaultProtocolClient(`codex-subscription-router`)", tools.packed_bundles["main-a.js"])
        self.assertIn("ui-test-bridge.cjs", tools.packed_bundles["main-a.js"])
        self.assertIn("ui-test-bridge.cjs", tools.packed_bundles)
        self.assertIn("disabled by Codex Subscription Router", tools.packed_bundles["x.js"])
        self.assertEqual(tools.unpack_dir, "node_modules/{@openai,node-pty}")
        token = (self.state_root / "control-token").read_text(encoding="utf-8")
        tools.patch_renderer.assert_called_once()
        self.assertEqual(tools.patch_renderer.call_args.args[1], token)
        # Helpers: exe-info on the official executable, set-asar-integrity on
        # the staged one with the repacked header digest.
        helpers = [(command, kwargs) for command, kwargs in tools.subprocess_runs if command[0] == "node"]
        (exe_info_command, exe_info_kwargs), (integrity_command, integrity_kwargs) = helpers
        self.assertEqual(Path(exe_info_command[2]), self.source / "ChatGPT.exe")
        self.assertEqual(exe_info_kwargs["errors"], "strict")
        self.assertTrue(Path(integrity_command[2]).parent.parent.name.startswith(win.STAGING_PREFIX))
        self.assertEqual(Path(integrity_command[2]).name, "ChatGPT.exe")
        self.assertEqual(integrity_command[3], "resources\\app.asar")
        self.assertEqual(integrity_command[4], patch_app.asar_header_digest(destination / "resources" / "app.asar"))
        self.assertEqual(integrity_kwargs["errors"], "strict")
        self.assertIn(f"Recorded asar header digest {integrity_command[4]}", tools.stdout)
        self.assertIn("Retargeted 1 protocol-client registration(s)", tools.stdout)
        self.assertIn("untested official ChatGPT build", tools.stderr)
        self.assertTrue(tools.stdout.rstrip().endswith(
            f"{destination}\n{destination / 'Codex Subscription Router.exe'}"
        ))

    def assert_source_checks_ran_before_the_copy(self, tools):
        names = [name for name, _ in tools.events]
        for check in ("unpack_globs", "locate_codex_executable", "asar_command"):
            self.assertLess(names.index(check), names.index("exe-info"), names)
            self.assertLess(names.index(check), names.index("build"), names)
        self.assertLess(names.index("asar_command"), names.index("exe-info"), names)
        self.assertEqual(names.count("unpack_globs"), 1)
        self.assertEqual(names.count("locate_codex_executable"), 1)
        arguments = dict(tools.events)
        self.assertEqual(arguments["unpack_globs"], self.source)
        self.assertEqual(arguments["locate_codex_executable"], self.source)

    def test_first_install_discovers_the_source_and_creates_the_shortcut(self):
        tools = self.run_patch()
        self.assert_installed(tools)
        self.assert_source_checks_ran_before_the_copy(tools)
        self.assertFalse((self.state_root / "backups").exists())
        self.assertEqual(sorted(entry.name for entry in self.state_root.iterdir()), ["control-token"])
        self.assertFalse(any(command[0] == "powershell" for command, _ in tools.subprocess_runs))
        shortcut_dir = self.appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        self.assertTrue(shortcut_dir.is_dir())
        shortcut_commands = [command for command in tools.run_commands if command[0] == "powershell"]
        self.assertEqual(len(shortcut_commands), 1)
        self.assertIn(str(self.destination / "Codex Subscription Router.exe"), shortcut_commands[0][4])
        self.assertIn(str(shortcut_dir / "Codex Subscription Router.lnk"), shortcut_commands[0][4])
        self.assertIn("Start menu shortcut:", tools.stdout)

    def test_force_backs_up_the_previous_install_after_the_process_check(self):
        make_app(self.destination, "ChatGPT.exe")
        (self.destination / "marker").write_text("previous", encoding="utf-8")
        tools = self.run_patch(force=True, create_shortcut=False)
        self.assert_installed(tools)
        self.assertFalse((self.destination / "marker").exists())
        backups = sorted((self.state_root / "backups").iterdir())
        self.assertEqual(len(backups), 1)
        self.assertRegex(backups[0].name, r"^\d{8}-\d{6}$")
        self.assertEqual(sorted(entry.name for entry in backups[0].iterdir()), ["Codex Subscription Router"])
        self.assertEqual(
            (backups[0] / "Codex Subscription Router" / "marker").read_text(encoding="utf-8"), "previous"
        )
        self.assertIn(f"Existing copy moved to {backups[0] / 'Codex Subscription Router'}", tools.stdout)
        process_checks = [(command, kwargs) for command, kwargs in tools.subprocess_runs if command[0] == "powershell"]
        self.assertEqual(len(process_checks), 1)
        self.assertIn(str(self.destination) + "\\", process_checks[0][0][4])
        self.assertEqual(process_checks[0][1]["errors"], "replace")
        self.assertFalse(any(command[0] == "powershell" for command in tools.run_commands))
        self.assertNotIn("Start menu shortcut", tools.stdout)

    def test_existing_destination_without_force_stops_before_any_copy(self):
        make_app(self.destination, "ChatGPT.exe")
        (self.destination / "marker").write_text("previous", encoding="utf-8")
        with self.assertRaises(RuntimeError) as caught:
            self.run_patch()
        self.assertIn("pass --force", str(caught.exception))
        self.assertEqual((self.destination / "marker").read_text(encoding="utf-8"), "previous")
        self.assertEqual(sorted(entry.name for entry in self.destination.parent.iterdir()), ["ChatGPT", "Codex Subscription Router"])
        self.assertFalse((self.state_root / "backups").exists())

    def test_unknown_source_layout_stops_before_tools_copy_or_builds(self):
        # A parked codex.real.exe beside the bundled codex.exe is refused on the
        # source itself, before go/node are probed or anything is written.
        (self.source / self.codex_relative).with_name("codex.real.exe").write_bytes(b"MZ")
        with self.assertRaises(RuntimeError) as caught:
            tools = self.run_patch()
        self.assertIn("already contains codex.real.exe", str(caught.exception))
        self.assertFalse(self.destination.exists())
        self.assertFalse(self.state_root.exists())
        self.assertEqual(sorted(entry.name for entry in self.local.joinpath("Programs").iterdir()), ["ChatGPT"])

    def test_explicit_source_and_codex_override(self):
        other = make_app(self.root / "elsewhere" / "ChatGPT", "ChatGPT.exe", unpacked={"@openai": {}, "node-pty": {}})
        (other / "resources" / "app.asar").write_bytes(b"official asar")
        (other / self.codex_relative).parent.mkdir(parents=True)
        (other / self.codex_relative).write_bytes(b"MZ official codex")
        self.source = other
        tools = self.run_patch(source=other, codex_executable=self.codex_relative.as_posix())
        self.assert_installed(tools)
        self.assert_source_checks_ran_before_the_copy(tools)


if __name__ == "__main__":
    unittest.main()
