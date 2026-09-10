"""Windows patcher tests; run on any OS, no Windows tools or official app required."""
import json
import subprocess
import sys
import tempfile
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
        self.assertTrue(hasattr(win, "patch_app"))
        self.assertIs(win.shared, patch_app)

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
            'process.env.SKY_CUA_SERVICE_NATIVE_PIPE_PATH="\\\\\\\\.\\\\pipe\\\\codex-subscription-router-computer-use";'
            "process.env.CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH=`1`;",
        )

    def test_prelude_is_a_javascript_string_for_the_pipe(self):
        prelude = win.windows_desktop_profile_prelude()
        literal = prelude.split("=", 1)[1].split(";", 1)[0]
        self.assertEqual(json.loads(literal), r"\\.\pipe\codex-subscription-router-computer-use")

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
        self.assertTrue(run.call_args.kwargs["check"])

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

    def test_icacls_command(self):
        self.assertEqual(
            win.icacls_command(Path("C:\\Users\\me\\.codex-mux"), "PC\\me"),
            [
                "icacls", "C:\\Users\\me\\.codex-mux", "/inheritance:r", "/grant:r",
                "PC\\me:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F",
            ],
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


if __name__ == "__main__":
    unittest.main()
