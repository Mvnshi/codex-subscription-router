"""Signing regression tests; no signing certificate or macOS tools required."""
import hashlib
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import patch_app


class SigningTeamTests(unittest.TestCase):
    def resolve(self, identity, metadata=("true", "TEAMID5678")):
        with mock.patch.object(patch_app.shutil, "copyfile") as copy, \
             mock.patch.object(patch_app, "run") as run, \
             mock.patch.object(patch_app, "signed_code_metadata", return_value=metadata):
            result = patch_app.signing_team_identifier(identity)
        return result, copy, run

    def test_development_name_suffix_is_not_the_team(self):
        result, copy, run = self.resolve("Apple Development: Example (PERSON1234)")
        self.assertEqual(result, "TEAMID5678")
        self.assertEqual(str(copy.call_args.args[0]), "/usr/bin/true")
        self.assertIn("Apple Development: Example (PERSON1234)", run.call_args.args[0])

    def test_certificate_fingerprint_selector_is_preserved(self):
        fingerprint = "A" * 40
        result, _, run = self.resolve(fingerprint)
        self.assertEqual(result, "TEAMID5678")
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--sign") + 1], fingerprint)

    def test_developer_id_uses_actual_signature_team(self):
        result, _, _ = self.resolve("Developer ID Application: Example (TEAMID5678)")
        self.assertEqual(result, "TEAMID5678")

    def test_adhoc_does_not_invoke_signing_tools(self):
        with mock.patch.object(patch_app, "run") as run, \
             mock.patch.object(patch_app.shutil, "copyfile") as copy:
            self.assertIsNone(patch_app.signing_team_identifier("-"))
        run.assert_not_called()
        copy.assert_not_called()

    def test_missing_or_invalid_team_fails_closed(self):
        for team in (None, "not set", "short", "TEAMID5678\nother"):
            with self.subTest(team=team), self.assertRaises(RuntimeError):
                self.resolve("Apple Development: Example (PERSON1234)", ("true", team))

    def test_signing_failure_is_not_replaced_by_name_suffix(self):
        with mock.patch.object(patch_app.shutil, "copyfile"), \
             mock.patch.object(patch_app, "run", side_effect=subprocess.CalledProcessError(1, "codesign")), \
             self.assertRaises(subprocess.CalledProcessError):
            patch_app.signing_team_identifier("Apple Development: Example (PERSON1234)")


BUILD_8109 = ("26.901.51231", "8109")
BUILD_7746 = ("26.901.22334", "7746")


class SupportedBuildTests(unittest.TestCase):
    """Guard the per-build tables that gate patching a new desktop release."""

    def test_build_8109_is_approved_with_its_asar_hash(self):
        self.assertEqual(
            patch_app.TESTED_SOURCE_BUILDS[BUILD_8109],
            "64fc2f27d2dddfa968acfacbe5e4e0328071bdc406351ff4a7d18f0b4692c83d",
        )

    def test_every_tested_build_has_complete_expectations(self):
        # A half-added build would otherwise fall back to the module defaults
        # and patch a release nobody verified.
        expected = set(patch_app.TESTED_SOURCE_BUILDS)
        for table in (
            patch_app.EXPECTED_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD,
            patch_app.EXPECTED_CUA_SERVICE_LAYOUT_BY_BUILD,
            patch_app.EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD,
        ):
            with self.subTest(table=table):
                self.assertEqual(set(table), expected)

    def test_build_8109_computer_use_expectations(self):
        self.assertEqual(
            patch_app.EXPECTED_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD[BUILD_8109], 49
        )
        self.assertEqual(
            patch_app.EXPECTED_ASAR_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD[BUILD_8109], 16
        )
        self.assertEqual(
            patch_app.EXPECTED_CUA_SERVICE_LAYOUT_BY_BUILD[BUILD_8109],
            patch_app.DEFAULT_CUA_SERVICE_LAYOUT,
        )

    def test_build_8109_counts_exclude_provisioning_profiles(self):
        # 8109 carries 53 raw references, but patch_computer_use_identity
        # deletes both embedded.provisionprofile files (4 references) before
        # counting, which leaves the same 49 as 7746. Counting the raw total
        # here would abort every install against the newer app.
        self.assertEqual(
            patch_app.EXPECTED_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD[BUILD_8109],
            patch_app.EXPECTED_CUA_IDENTIFIER_REPLACEMENTS_BY_BUILD[BUILD_7746],
        )

    def test_source_hashes_are_distinct_per_build(self):
        hashes = list(patch_app.TESTED_SOURCE_BUILDS.values())
        self.assertEqual(len(hashes), len(set(hashes)))


class AsarNodeRuntimeTests(unittest.TestCase):
    """asar 4.x needs node >= 22.12; fail before a confusing mid-extract abort."""

    MANIFEST = {"version": "4.2.1", "engines": {"node": ">=22.12.0"}}

    def check(self, reported):
        completed = subprocess.CompletedProcess([], 0, stdout=reported, stderr="")
        with mock.patch.object(patch_app.subprocess, "run", return_value=completed):
            patch_app.require_asar_node_runtime(self.MANIFEST)

    def test_old_node_is_rejected_with_actionable_message(self):
        with self.assertRaises(RuntimeError) as caught:
            self.check("v20.20.2\n")
        message = str(caught.exception)
        self.assertIn("20.20.2", message)
        self.assertIn(">=22.12.0", message)

    def test_supported_node_passes(self):
        for reported in ("v22.12.0\n", "v24.16.0\n"):
            with self.subTest(reported=reported):
                self.check(reported)

    def test_missing_node_does_not_mask_the_real_failure(self):
        with mock.patch.object(patch_app.subprocess, "run", side_effect=OSError):
            patch_app.require_asar_node_runtime(self.MANIFEST)

    def test_manifest_without_an_engine_range_is_ignored(self):
        with mock.patch.object(patch_app.subprocess, "run") as run:
            patch_app.require_asar_node_runtime({"version": "4.2.1"})
        run.assert_not_called()


class AsarIntegrityTests(unittest.TestCase):
    """ElectronAsarIntegrity records the header digest, not the file digest."""

    def build_asar(self, header: bytes, payload: bytes) -> Path:
        directory = tempfile.mkdtemp()
        path = Path(directory) / "app.asar"
        prefix = struct.pack("<IIII", 4, len(header) + 8, len(header) + 4, len(header))
        path.write_bytes(prefix + header + payload)
        return path

    def test_digest_covers_the_header_only(self):
        header = b'{"files":{"index.js":{"size":3,"offset":"0"}}}'
        path = self.build_asar(header, b"abc")
        self.assertEqual(
            patch_app.asar_header_digest(path), hashlib.sha256(header).hexdigest()
        )

    def test_payload_changes_do_not_change_the_digest(self):
        header = b'{"files":{"index.js":{"size":3,"offset":"0"}}}'
        first = patch_app.asar_header_digest(self.build_asar(header, b"abc"))
        second = patch_app.asar_header_digest(self.build_asar(header, b"xyz"))
        self.assertEqual(first, second)

    def test_whole_file_digest_is_not_used(self):
        header = b'{"files":{}}'
        payload = b"payload"
        path = self.build_asar(header, payload)
        self.assertNotEqual(
            patch_app.asar_header_digest(path),
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )

    def test_truncated_archive_is_rejected(self):
        directory = tempfile.mkdtemp()
        path = Path(directory) / "app.asar"
        path.write_bytes(b"\x00\x01")
        with self.assertRaises(RuntimeError):
            patch_app.asar_header_digest(path)


COMPUTER_USE_APP = Path("/opt/tester/Applications/Codex Subscription Router Computer Use.app")
STATE_ROOT = Path("/opt/tester/.codex-mux")
PROFILE_CALL = (
    "Xe.app.setPath(`userData`,Qt({appDataPath:Xe.app.getPath(`appData`),"
    "buildFlavor:`prod`,env:process.env}))"
)
BOOTSTRAP_SOURCE = (
    "import*as Xe from`electron`;" + PROFILE_CALL + ";"
    "await Up.initialize();try{let{runMainAppStartup:Rm}=await import(`./main-a.js`);"
    "await Rm()}catch(e){Xe.app.exit(1)}"
)
MAIN_SOURCE = (
    "kc=new Rf(Wv(Ku.codexHome),{onServiceAvailable:()=>{}});"
    "const Ti=`Control desktop apps on macOS through Computer Use.`;"
)
UPDATER_LIFECYCLE_SOURCE = (
    "class Sp{initializeUpdater(){return this.options.enableUpdater?"
    "(this.updaterInitialization??=this.initializeUpdaterOnce(),"
    "this.updaterInitialization):Promise.resolve()}}"
)
# Derived by hand from the pre-refactor patch_desktop_profile: four env
# assignments (json.dumps quoting), the userData rewrite, the removed
# `await Up.initialize();`, and the untouched tail.
EXPECTED_BOOTSTRAP = (
    "import*as Xe from`electron`;"
    'process.env.SKY_CUA_SERVICE_NATIVE_PIPE_PATH="/opt/tester/.codex-mux/computer-use.sock";'
    'process.env.SKY_CUA_SERVICE_PATH="/opt/tester/Applications/Codex Subscription Router Computer Use.app";'
    'process.env.CODEX_ELECTRON_COMPUTER_USE_APP_PATH="/opt/tester/Applications/Codex Subscription Router Computer Use.app";'
    "process.env.CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH=`1`;"
    "Xe.app.setPath(`userData`,Xe.app.getPath(`appData`)+`/Codex Subscription Router`);"
    "try{let{runMainAppStartup:Rm}=await import(`./main-a.js`);"
    "await Rm()}catch(e){Xe.app.exit(1)}"
)
EXPECTED_MAIN = (
    'kc=new Rf("/opt/tester/Applications/Codex Subscription Router Computer Use.app",'
    "{onServiceAvailable:()=>{}});"
    "const Ti=`Control desktop apps on macOS through Computer Use via node_repl and "
    "@oai/sky only. Never use shell commands, open, AppleScript, osascript, "
    "JXA, System Events, or CGEvent synthesis for computer interactions or "
    "as a fallback. If Computer Use is unavailable, report the failure "
    "instead of using another automation method.`;"
    "\n;if(process.env.CODEX_MUX_UI_TESTS===`1`)"
    "require(require(`node:path`).join(__dirname,`ui-test-bridge.cjs`)).start();"
)
EXPECTED_UPDATER_LIFECYCLE = (
    "class Sp{initializeUpdater(){return this.lastUnavailableReason="
    "`disabled by Codex Subscription Router`,Promise.resolve()}}"
)


class DesktopProfilePatchTests(unittest.TestCase):
    """The main-process patch must stay byte-identical across refactors."""

    def make_tree(self, bootstrap=BOOTSTRAP_SOURCE, main=MAIN_SOURCE) -> Path:
        extracted = Path(tempfile.mkdtemp()) / "asar"
        build = extracted / ".vite" / "build"
        build.mkdir(parents=True)
        if bootstrap is not None:
            (build / "bootstrap-a.js").write_text(bootstrap, encoding="utf-8")
        if main is not None:
            (build / "main-a.js").write_text(main, encoding="utf-8")
        (build / "x.js").write_text(UPDATER_LIFECYCLE_SOURCE, encoding="utf-8")
        return extracted

    def patch(self, extracted: Path) -> None:
        with mock.patch.object(patch_app, "DEFAULT_STATE_ROOT", STATE_ROOT):
            patch_app.patch_desktop_profile(extracted, COMPUTER_USE_APP)

    def test_patch_desktop_profile_output_is_exact(self):
        extracted = self.make_tree()
        self.patch(extracted)
        build = extracted / ".vite" / "build"
        self.assertEqual(
            (build / "bootstrap-a.js").read_text(encoding="utf-8"), EXPECTED_BOOTSTRAP
        )
        self.assertEqual((build / "main-a.js").read_text(encoding="utf-8"), EXPECTED_MAIN)
        self.assertEqual(
            (build / "x.js").read_text(encoding="utf-8"), EXPECTED_UPDATER_LIFECYCLE
        )
        self.assertEqual(
            (build / "ui-test-bridge.cjs").read_bytes(),
            (patch_app.PROJECT_ROOT / "ui" / "ui-test-bridge.cjs").read_bytes(),
        )

    def test_bundle_count_errors_are_unchanged(self):
        extracted = self.make_tree()
        (extracted / ".vite" / "build" / "bootstrap-b.js").write_text(
            BOOTSTRAP_SOURCE, encoding="utf-8"
        )
        with self.assertRaises(RuntimeError) as caught:
            self.patch(extracted)
        self.assertEqual(
            str(caught.exception), "expected one ChatGPT bootstrap bundle, found 2"
        )
        extracted = self.make_tree(main=None)
        with self.assertRaises(RuntimeError) as caught:
            self.patch(extracted)
        self.assertEqual(
            str(caught.exception), "expected one ChatGPT desktop main bundle, found 0"
        )
        # The bootstrap was already rewritten when the main check failed,
        # exactly as before the split.
        self.assertEqual(
            (extracted / ".vite" / "build" / "bootstrap-a.js").read_text(encoding="utf-8"),
            EXPECTED_BOOTSTRAP,
        )

    def test_missing_anchors_fail_closed(self):
        extracted = self.make_tree(bootstrap=BOOTSTRAP_SOURCE.replace("buildFlavor", "flavour"))
        with self.assertRaises(RuntimeError) as caught:
            self.patch(extracted)
        self.assertEqual(
            str(caught.exception), "could not isolate the copied ChatGPT desktop profile"
        )
        extracted = self.make_tree(main=MAIN_SOURCE.replace("codexHome", "home"))
        with self.assertRaises(RuntimeError) as caught:
            self.patch(extracted)
        self.assertEqual(
            str(caught.exception),
            "could not pin the managed Computer Use service to its installed app",
        )
        extracted = self.make_tree(main=MAIN_SOURCE.replace("on macOS", "on Mac"))
        with self.assertRaises(RuntimeError) as caught:
            self.patch(extracted)
        self.assertEqual(
            str(caught.exception), "could not find the Computer Use tool instruction"
        )


class DesktopProfileStepTests(unittest.TestCase):
    """The split steps compose to the same bytes and are reusable by the Windows port."""

    def make_tree(self) -> Path:
        extracted = Path(tempfile.mkdtemp()) / "asar"
        build = extracted / ".vite" / "build"
        build.mkdir(parents=True)
        (build / "bootstrap-a.js").write_text(BOOTSTRAP_SOURCE, encoding="utf-8")
        (build / "main-a.js").write_text(MAIN_SOURCE, encoding="utf-8")
        (build / "x.js").write_text(UPDATER_LIFECYCLE_SOURCE, encoding="utf-8")
        return extracted

    def test_macos_prelude_is_the_exact_pre_refactor_text(self):
        with mock.patch.object(patch_app, "DEFAULT_STATE_ROOT", STATE_ROOT):
            prelude = patch_app.macos_desktop_profile_prelude(COMPUTER_USE_APP)
        self.assertEqual(
            prelude,
            'process.env.SKY_CUA_SERVICE_NATIVE_PIPE_PATH="/opt/tester/.codex-mux/computer-use.sock";'
            'process.env.SKY_CUA_SERVICE_PATH="/opt/tester/Applications/Codex Subscription Router Computer Use.app";'
            'process.env.CODEX_ELECTRON_COMPUTER_USE_APP_PATH="/opt/tester/Applications/Codex Subscription Router Computer Use.app";'
            "process.env.CODEX_ELECTRON_SKIP_COMPUTER_USE_CANONICAL_REFRESH=`1`;",
        )

    def test_isolate_desktop_profile_takes_a_caller_prelude(self):
        extracted = self.make_tree()
        patch_app.isolate_desktop_profile(extracted, "PRELUDE;")
        build = extracted / ".vite" / "build"
        self.assertEqual(
            (build / "bootstrap-a.js").read_text(encoding="utf-8"),
            "import*as Xe from`electron`;PRELUDE;"
            "Xe.app.setPath(`userData`,Xe.app.getPath(`appData`)+`/Codex Subscription Router`);"
            "try{let{runMainAppStartup:Rm}=await import(`./main-a.js`);"
            "await Rm()}catch(e){Xe.app.exit(1)}",
        )
        # The updater lifecycle is disabled as part of isolation, not later.
        self.assertEqual(
            (build / "x.js").read_text(encoding="utf-8"), EXPECTED_UPDATER_LIFECYCLE
        )
        # main-a.js is untouched by this step.
        self.assertEqual((build / "main-a.js").read_text(encoding="utf-8"), MAIN_SOURCE)

    def test_pin_then_bridge_reproduce_the_main_bundle(self):
        extracted = self.make_tree()
        patch_app.pin_managed_computer_use(extracted, COMPUTER_USE_APP)
        patch_app.install_ui_test_bridge(extracted)
        build = extracted / ".vite" / "build"
        self.assertEqual((build / "main-a.js").read_text(encoding="utf-8"), EXPECTED_MAIN)
        self.assertTrue((build / "ui-test-bridge.cjs").is_file())

    def test_install_ui_test_bridge_alone_only_appends(self):
        extracted = self.make_tree()
        patch_app.install_ui_test_bridge(extracted)
        self.assertEqual(
            (extracted / ".vite" / "build" / "main-a.js").read_text(encoding="utf-8"),
            MAIN_SOURCE
            + "\n;if(process.env.CODEX_MUX_UI_TESTS===`1`)"
            "require(require(`node:path`).join(__dirname,`ui-test-bridge.cjs`)).start();",
        )

    def test_single_bundle_messages(self):
        extracted = self.make_tree()
        self.assertEqual(
            patch_app.single_bundle(extracted, "main-*.js", "ChatGPT desktop main bundle").name,
            "main-a.js",
        )
        with self.assertRaises(RuntimeError) as caught:
            patch_app.single_bundle(extracted, "renderer-*.js", "renderer bundle")
        self.assertEqual(str(caught.exception), "expected one renderer bundle, found 0")


class GoBuildTests(unittest.TestCase):
    """build_go_program must compose the exact command macOS always ran."""

    def build(self, *args, **kwargs):
        destination = Path(tempfile.mkdtemp()) / "out" / "binary"
        destination.parent.mkdir()
        destination.write_bytes(b"")
        with mock.patch.object(patch_app.subprocess, "run") as run:
            patch_app.build_go_program(destination, *args, **kwargs)
        return destination, run.call_args

    def test_build_proxy_command_is_unchanged(self):
        destination = Path(tempfile.mkdtemp()) / "codex-mux"
        destination.write_bytes(b"")
        with mock.patch.object(patch_app.subprocess, "run") as run:
            patch_app.build_proxy(destination)
        self.assertEqual(
            run.call_args.args[0],
            ["go", "build", "-trimpath", "-ldflags=-s -w", "-o", str(destination), "./cmd/codex-mux"],
        )
        self.assertEqual(run.call_args.kwargs["cwd"], patch_app.PROJECT_ROOT)
        self.assertIsNone(run.call_args.kwargs["env"])
        self.assertTrue(run.call_args.kwargs["check"])
        self.assertTrue(destination.stat().st_mode & 0o111)

    def test_cross_compile_sets_only_the_requested_variables(self):
        destination, call = self.build(
            "./cmd/codex-router-launcher",
            goos="windows",
            ldflags="-s -w -H=windowsgui -X main.electronExecutable=ChatGPT.exe",
        )
        self.assertEqual(
            call.args[0],
            [
                "go", "build", "-trimpath",
                "-ldflags=-s -w -H=windowsgui -X main.electronExecutable=ChatGPT.exe",
                "-o", str(destination), "./cmd/codex-router-launcher",
            ],
        )
        env = call.kwargs["env"]
        self.assertEqual(env["GOOS"], "windows")
        self.assertNotIn("GOARCH", {k for k in env if k not in patch_app.os.environ})
        self.assertEqual(env.get("PATH"), patch_app.os.environ.get("PATH"))

    def test_goarch_can_be_pinned(self):
        _, call = self.build("./cmd/codex-mux", goos="windows", goarch="arm64")
        self.assertEqual(call.kwargs["env"]["GOOS"], "windows")
        self.assertEqual(call.kwargs["env"]["GOARCH"], "arm64")


if __name__ == "__main__":
    unittest.main()
