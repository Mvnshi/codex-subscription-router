"""Tests for the canary result helper, using log shapes copied from real runs."""
import json
import tempfile
import unittest
from pathlib import Path

import canary

WINDOWS_PASS = """==> Checking the Codex app on this PC
Source is a Microsoft Store (MSIX) install; it is copied read-only and the official package stays untouched.
Source install: C:\\Program Files\\WindowsApps\\OpenAI.Codex_26.930.7945.0_x64__2p2nqsd0c76g0\\app
Source Codex version: 154.0.8037.98 (file 154.0.8037.98), x64, signed, app.asar 611d6da979d8bbabfec97dd90dcce27a9522e7016e6ccf135d59cab693ab08da

==> Building Codex Router (this takes a few minutes)
Gave the copy its own tray-icon identity (4 GUID(s))
Codex Router is installed.
"""

MAC_FAIL = """\x1b[1mSource ChatGPT version: 26.930.61225 (13232), app.asar 88b8cce6f627771bf341f5a6bb464ad220749b0d442d44f618d7741c2de7318b\x1b[0m
Building multiplexer\u2026
Copying ChatGPT.app\u2026
Patching desktop profile and renderer\u2026
patch failed: expected 17 Computer Use references in app.asar, found 16

Install failed: the Windows patcher failed.
"""


class ExtractionTests(unittest.TestCase):
    def test_windows_identity(self):
        identity = canary.extract_identity(WINDOWS_PASS)
        self.assertEqual(identity["asar"], "611d6da979d8bbabfec97dd90dcce27a9522e7016e6ccf135d59cab693ab08da")
        self.assertEqual(identity["host_version"], "154.0.8037.98")

    def test_macos_identity_through_colour_codes_and_carriage_returns(self):
        identity = canary.extract_identity(MAC_FAIL.replace("\n", "\r\n"))
        self.assertEqual(identity["host_version"], "26.930.61225")
        self.assertEqual(identity["host_second"], "13232")
        self.assertTrue(identity["asar"].startswith("88b8cce6"))

    def test_the_last_identity_line_wins(self):
        log = MAC_FAIL + "Source ChatGPT version: 1.0 (2), app.asar " + "ab" * 32 + "\n"
        self.assertEqual(canary.extract_identity(log)["asar"], "ab" * 32)

    def test_no_identity_is_an_empty_dict(self):
        self.assertEqual(canary.extract_identity("nothing here"), {})

    def test_the_last_failure_message_is_the_one_reported(self):
        self.assertEqual(canary.extract_failure(MAC_FAIL), "the Windows patcher failed.")
        self.assertEqual(canary.extract_failure("patch failed: first\npatch failed: second\n"), "second")
        self.assertEqual(canary.extract_failure(WINDOWS_PASS), "")


class ResultTests(unittest.TestCase):
    def test_a_clean_windows_run_that_booted_passes(self):
        result = canary.build_result(
            platform="windows-x64", log=WINDOWS_PASS, exit_code=0, boot="ok",
            package="26.930.7945.0", app_version="26.930.61225", build="13232",
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["app_version"], "26.930.61225")
        self.assertEqual(result["build"], "13232")
        self.assertEqual(result["package"], "26.930.7945.0")
        self.assertRegex(result["asar"], r"^611d6da9")
        self.assertEqual(result["message"], "")

    def test_windows_does_not_mistake_chromium_for_the_app_version(self):
        result = canary.build_result(platform="windows-x64", log=WINDOWS_PASS, exit_code=0, boot="ok")
        self.assertEqual(result["app_version"], "")

    def test_a_patch_failure_on_macos_reports_the_first_error_and_the_build(self):
        result = canary.build_result(platform="macos", log=MAC_FAIL, exit_code=1)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["app_version"], "26.930.61225")
        self.assertEqual(result["build"], "13232")
        self.assertEqual(result["message"], "the Windows patcher failed.")

    def test_patched_but_did_not_boot(self):
        result = canary.build_result(
            platform="windows-x64", log=WINDOWS_PASS, exit_code=0, boot="failed", boot_detail="control API never answered"
        )
        self.assertEqual(result["status"], "boot-failed")
        self.assertEqual(result["message"], "control API never answered")

    def test_nothing_to_test_is_an_error_not_a_failure_of_the_patch(self):
        result = canary.build_result(platform="windows-arm64", log="winget: no such package", exit_code=1)
        self.assertEqual(result["status"], "error")
        self.assertIn("exited with code 1", result["message"])

    def test_a_skipped_boot_still_passes(self):
        result = canary.build_result(platform="macos", log=WINDOWS_PASS, exit_code=0, boot="skipped")
        self.assertEqual(result["status"], "pass")


class OutputTests(unittest.TestCase):
    def test_summary_table(self):
        results = [
            canary.build_result(platform="windows-x64", log=WINDOWS_PASS, exit_code=0, boot="ok", package="26.930.7945.0"),
            canary.build_result(platform="macos", log=MAC_FAIL, exit_code=1),
        ]
        text = canary.summary(results)
        self.assertIn("| macos | `26.930.61225 (13232)` | FAIL | the Windows patcher failed. |", text)
        self.assertIn("| windows-x64 | `26.930.7945.0` | pass | boot: ok |", text)

    def test_cli_writes_a_result_file(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "install.log"
            log.write_text(WINDOWS_PASS, encoding="utf-8")
            out = Path(directory) / "result.json"
            code = canary.main(
                ["result", "--platform", "windows-x64", "--log", str(log), "--exit-code", "0", "--boot", "ok",
                 "--package", "26.930.7945.0", "--out", str(out)]
            )
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["status"], "pass")

    def test_a_missing_log_is_treated_as_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "result.json"
            canary.main(["result", "--platform", "macos", "--log", str(Path(directory) / "none.log"), "--exit-code", "1", "--out", str(out)])
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["status"], "error")


if __name__ == "__main__":
    unittest.main()
