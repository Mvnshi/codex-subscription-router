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


if __name__ == "__main__":
    unittest.main()
