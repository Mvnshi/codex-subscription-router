"""Tests for recording a canary-verified build."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import record_build as rb

ASAR_A = "a1" * 32
ASAR_B = "b2" * 32

DATA = {
    "windows": {
        "packages": {
            "26.930.3930.0": {
                "app": "26.930.31730", "build": "12947", "level": "tested",
                "host": ["154.0.8037.98", "154.0.8037.98"], "asar": {"x64": "f0" * 32},
            }
        }
    },
    "macos": {"builds": {}},
}

WINDOWS_RESULT = {
    "platform": "windows-x64", "status": "pass", "asar": ASAR_A, "package": "26.930.7945.0",
    "app_version": "26.930.61225", "build": "13232", "host_version": "154.0.8037.98", "host_second": "154.0.8037.98",
}
MAC_RESULT = {"platform": "macos", "status": "pass", "asar": ASAR_A, "app_version": "26.930.61225", "build": "13232"}


class RecordTests(unittest.TestCase):
    def data(self):
        return copy.deepcopy(DATA)

    def test_a_windows_build_is_recorded_as_verified(self):
        data = self.data()
        message = rb.record(WINDOWS_RESULT, data)
        self.assertIn("26.930.7945.0 (x64) as verified", message)
        entry = data["windows"]["packages"]["26.930.7945.0"]
        self.assertEqual(entry["level"], "verified")
        self.assertEqual(entry["asar"], {"x64": ASAR_A})
        self.assertEqual(entry["host"], ["154.0.8037.98", "154.0.8037.98"])

    def test_the_arm64_package_of_a_recorded_version_is_added_beside_it(self):
        data = self.data()
        rb.record(WINDOWS_RESULT, data)
        message = rb.record(dict(WINDOWS_RESULT, platform="windows-arm64", asar=ASAR_B), data)
        self.assertIn("added the arm64 package", message)
        self.assertEqual(data["windows"]["packages"]["26.930.7945.0"]["asar"], {"x64": ASAR_A, "arm64": ASAR_B})

    def test_recording_twice_changes_nothing(self):
        data = self.data()
        rb.record(WINDOWS_RESULT, data)
        before = copy.deepcopy(data)
        self.assertIn("already recorded", rb.record(WINDOWS_RESULT, data))
        self.assertEqual(data, before)

    def test_a_tested_build_stays_tested(self):
        data = self.data()
        result = dict(WINDOWS_RESULT, package="26.930.3930.0", app_version="26.930.31730", build="12947", asar="f0" * 32)
        self.assertIn("already recorded (tested)", rb.record(result, data))
        self.assertEqual(data["windows"]["packages"]["26.930.3930.0"]["level"], "tested")

    def test_a_different_hash_for_a_recorded_package_is_refused(self):
        data = self.data()
        rb.record(WINDOWS_RESULT, data)
        with self.assertRaises(rb.RecordError) as caught:
            rb.record(dict(WINDOWS_RESULT, asar=ASAR_B), data)
        self.assertIn("different app.asar hash", str(caught.exception))

    def test_a_package_recorded_as_another_app_build_is_refused(self):
        data = self.data()
        rb.record(WINDOWS_RESULT, data)
        with self.assertRaises(rb.RecordError):
            rb.record(dict(WINDOWS_RESULT, build="99999"), data)

    def test_only_a_passing_result_is_recorded(self):
        for status in ("fail", "boot-failed", "error"):
            with self.subTest(status=status), self.assertRaises(rb.RecordError):
                rb.record(dict(WINDOWS_RESULT, status=status), self.data())

    def test_missing_facts_are_refused(self):
        for field in ("asar", "package", "app_version", "build", "host_version"):
            with self.subTest(field=field), self.assertRaises(rb.RecordError):
                rb.record(dict(WINDOWS_RESULT, **{field: ""}), self.data())
        with self.assertRaises(rb.RecordError):
            rb.record(dict(WINDOWS_RESULT, asar="not a hash"), self.data())
        with self.assertRaises(rb.RecordError):
            rb.record(dict(MAC_RESULT, build=""), self.data())
        with self.assertRaises(rb.RecordError):
            rb.record(dict(MAC_RESULT, platform="linux"), self.data())

    def test_a_macos_build_is_keyed_by_version_and_build(self):
        data = self.data()
        self.assertIn("macOS 26.930.61225/13232", rb.record(MAC_RESULT, data))
        self.assertEqual(data["macos"]["builds"]["26.930.61225/13232"], {"level": "verified", "asar": ASAR_A})
        with self.assertRaises(rb.RecordError):
            rb.record(dict(MAC_RESULT, asar=ASAR_B), data)


class DocsTests(unittest.TestCase):
    def test_the_table_lists_newest_first_with_what_each_level_means(self):
        data = copy.deepcopy(DATA)
        rb.record(WINDOWS_RESULT, data)
        rb.record(MAC_RESULT, data)
        table = rb.render_table(data)
        lines = table.splitlines()
        self.assertTrue(lines[2].startswith("| Windows x64 | `OpenAI.Codex` `26.930.7945.0`"))
        self.assertIn("verified by the canary", lines[2])
        self.assertTrue(lines[3].startswith("| Windows x64 | `OpenAI.Codex` `26.930.3930.0`"))
        self.assertIn("tested by hand", lines[3])
        self.assertTrue(lines[4].startswith("| macOS | ChatGPT `26.930.61225`"))

    def test_only_the_section_between_the_markers_is_replaced(self):
        text = f"before\n{rb.TABLE_BEGIN}\nold table\n{rb.TABLE_END}\nafter\n"
        updated = rb.update_docs(text, DATA)
        self.assertTrue(updated.startswith("before\n" + rb.TABLE_BEGIN + "\n| Platform"))
        self.assertTrue(updated.endswith(rb.TABLE_END + "\nafter\n"))
        self.assertNotIn("old table", updated)

    def test_a_document_without_the_markers_is_an_error(self):
        with self.assertRaises(rb.RecordError):
            rb.update_docs("no markers", DATA)

    def test_the_committed_docs_table_matches_the_committed_data(self):
        data = json.loads(rb.DATA_FILE.read_text(encoding="utf-8"))
        docs = rb.DOCS_FILE.read_text(encoding="utf-8")
        self.assertEqual(rb.update_docs(docs, data), docs, "run scripts/record_build.py or regenerate the table")


class CommandLineTests(unittest.TestCase):
    def run_main(self, result, dry_run=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            data_file, docs_file, result_file = root / "data.json", root / "docs.md", root / "result.json"
            data_file.write_text(json.dumps(DATA), encoding="utf-8")
            docs_file.write_text(f"x\n{rb.TABLE_BEGIN}\n{rb.TABLE_END}\n", encoding="utf-8")
            result_file.write_text(json.dumps(result), encoding="utf-8")
            with mock.patch.object(rb, "DATA_FILE", data_file), mock.patch.object(rb, "DOCS_FILE", docs_file), \
                 mock.patch.object(rb, "ROOT", root), mock.patch("builtins.print"), mock.patch("sys.stderr"):
                code = rb.main([str(result_file)] + (["--dry-run"] if dry_run else []))
            return code, json.loads(data_file.read_text(encoding="utf-8")), docs_file.read_text(encoding="utf-8")

    def test_writes_both_files(self):
        code, data, docs = self.run_main(WINDOWS_RESULT)
        self.assertEqual(code, 0)
        self.assertIn("26.930.7945.0", data["windows"]["packages"])
        self.assertIn("26.930.7945.0", docs)

    def test_dry_run_changes_nothing(self):
        code, data, docs = self.run_main(WINDOWS_RESULT, dry_run=True)
        self.assertEqual(code, 0)
        self.assertEqual(data, DATA)
        self.assertNotIn("26.930.7945.0", docs)

    def test_a_failing_result_exits_1_and_changes_nothing(self):
        code, data, _ = self.run_main(dict(WINDOWS_RESULT, status="fail"))
        self.assertEqual(code, 1)
        self.assertEqual(data, DATA)


if __name__ == "__main__":
    unittest.main()
