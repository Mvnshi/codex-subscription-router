"""Tests for the upstream watcher and the recorded-build tables it reads. Offline: the feeds are fixtures."""
import contextlib
import io
import json
import unittest
from unittest import mock

import patch_app
import patch_app_windows as win
import upstream_watch as watch

APPCAST = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">
  <channel>
    <title>ChatGPT</title>
    <item>
      <title>26.930.61225</title>
      <pubDate>Tue, 06 Oct 2026 01:39:31 +0000</pubDate>
      <sparkle:version>13232</sparkle:version>
      <sparkle:shortVersionString>26.930.61225</sparkle:shortVersionString>
      <enclosure url="https://example.test/ChatGPT-darwin-arm64-26.930.61225.zip" length="1" type="application/zip"/>
    </item>
    <item>
      <title>26.930.31730</title>
      <pubDate>Sat, 03 Oct 2026 02:55:51 +0000</pubDate>
      <sparkle:version>12947</sparkle:version>
      <sparkle:shortVersionString>26.930.31730</sparkle:shortVersionString>
      <enclosure url="https://example.test/ChatGPT-darwin-arm64-26.930.31730.zip" length="1" type="application/zip"/>
    </item>
    <item><title>broken, no build</title></item>
  </channel>
</rss>
"""


def catalog(*full_names):
    skus = [{"Sku": {"Properties": {"Packages": [{"PackageFullName": name} for name in full_names]}}}] * 2
    return json.dumps({"Products": [{"DisplaySkuAvailabilities": skus}]})


STORE = catalog(
    "OpenAI.Codex_26.930.7945.0_x64__2p2nqsd0c76g0",
    "OpenAI.Codex_26.930.7945.0_arm64__2p2nqsd0c76g0",
    "OpenAI.Codex_26.930.3930.0_x64__2p2nqsd0c76g0",
    "Some.Other_1.0.0.0_x64__abc",
)


class ParsingTests(unittest.TestCase):
    def test_appcast_items_in_published_order_and_broken_ones_skipped(self):
        builds = watch.parse_appcast(APPCAST)
        self.assertEqual([(b.version, b.build) for b in builds], [("26.930.61225", "13232"), ("26.930.31730", "12947")])
        self.assertTrue(builds[0].url.endswith("26.930.61225.zip"))

    def test_store_catalog_lists_each_package_once_oldest_first(self):
        packages = watch.parse_store_catalog(STORE)
        self.assertEqual(
            [(p.version, p.architecture) for p in packages],
            [("26.930.3930.0", "x64"), ("26.930.7945.0", "arm64"), ("26.930.7945.0", "x64")],
        )

    def test_versions_compare_numerically(self):
        self.assertGreater(watch.version_key("26.930.10000.0"), watch.version_key("26.930.9999.0"))


class EvaluationTests(unittest.TestCase):
    def evaluate(self, macos_recorded, windows_recorded):
        return watch.evaluate(
            watch.parse_appcast(APPCAST), watch.parse_store_catalog(STORE), macos_recorded, windows_recorded
        )

    def test_everything_recorded_needs_no_attention(self):
        report = self.evaluate(
            {("26.930.61225", "13232"): "tested"},
            {("26.930.7945.0", "x64"): "verified", ("26.930.7945.0", "arm64"): "verified"},
        )
        self.assertEqual(report["needs_attention"], [])
        self.assertEqual(report["macos"]["level"], "tested")
        self.assertEqual(report["windows"]["arm64"]["level"], "verified")

    def test_newest_builds_that_are_not_recorded_are_flagged_per_platform(self):
        report = self.evaluate({("26.930.31730", "12947"): "tested"}, {("26.930.3930.0", "x64"): "tested"})
        self.assertEqual(report["needs_attention"], ["macos", "windows-arm64", "windows-x64"])
        # One published build is newer than the newest recorded one.
        self.assertEqual(report["macos"]["builds_not_recorded"], 1)

    def test_an_older_recorded_build_does_not_cover_the_newest(self):
        report = self.evaluate({}, {("26.930.7945.0", "x64"): "tested"})
        self.assertEqual(report["needs_attention"], ["macos", "windows-arm64"])
        self.assertEqual(report["macos"]["builds_not_recorded"], 2)

    def test_empty_feeds_report_nothing_to_act_on(self):
        report = watch.evaluate([], [], {}, {})
        self.assertEqual(report["needs_attention"], [])
        self.assertIn("feed unavailable", watch.markdown(report))

    def test_markdown_says_what_each_status_means(self):
        text = watch.markdown(
            self.evaluate({("26.930.61225", "13232"): "verified"}, {("26.930.7945.0", "x64"): "tested"})
        )
        self.assertIn("recorded, verified by the canary", text)
        self.assertIn("recorded, tested", text)
        self.assertIn("NOT RECORDED", text)


class GatherTests(unittest.TestCase):
    def test_a_feed_that_cannot_be_read_is_reported_and_the_other_still_counts(self):
        def fetch(url):
            if url == watch.APPCAST_URL:
                raise OSError("connection reset")
            return STORE

        report = watch.gather(fetch)
        self.assertEqual(report["errors"], ["macOS appcast: connection reset"])
        self.assertIn("windows-x64", report["needs_attention"])
        self.assertNotIn("macos", report["needs_attention"])

    def test_main_exit_codes(self):
        recorded = {("26.930.7945.0", "x64"): "tested", ("26.930.7945.0", "arm64"): "tested"}
        feeds = {watch.APPCAST_URL: APPCAST, watch.STORE_CATALOG_URL: STORE}
        with mock.patch.object(watch, "fetch_text", feeds.get), \
             mock.patch.object(watch, "recorded_windows", return_value=recorded), \
             mock.patch.object(watch, "recorded_macos", return_value={("26.930.61225", "13232"): "tested"}):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(watch.main(["--check"]), 0)
        with mock.patch.object(watch, "fetch_text", feeds.get), \
             mock.patch.object(watch, "recorded_windows", return_value={}), \
             mock.patch.object(watch, "recorded_macos", return_value={}):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(watch.main(["--check"]), 1)
                self.assertEqual(watch.main([]), 0)
        # Nothing readable at all is its own status.
        def failing(url):
            raise OSError("offline")
        with mock.patch.object(watch, "fetch_text", failing), contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(watch.main([]), 2)


class RecordedTablesTests(unittest.TestCase):
    def test_every_windows_record_is_in_the_table_for_its_level(self):
        for package, record in win.WINDOWS_PACKAGE_RECORDS.items():
            table = {"tested": win.TESTED_WINDOWS_SOURCE_BUILDS, "verified": win.CANARY_VERIFIED_WINDOWS_SOURCE_BUILDS}[
                record["level"]
            ]
            hashes = {h for value in table.values() for h in ((value,) if isinstance(value, str) else value)}
            for architecture, digest in record["asar"].items():
                self.assertIn(digest, hashes, f"{package} {architecture} is not in the {record['level']} table")
                self.assertRegex(digest, r"^[0-9a-f]{64}$")

    def test_every_hash_in_the_windows_tables_has_a_record(self):
        recorded = {digest for record in win.WINDOWS_PACKAGE_RECORDS.values() for digest in record["asar"].values()}
        for table in (win.TESTED_WINDOWS_SOURCE_BUILDS, win.CANARY_VERIFIED_WINDOWS_SOURCE_BUILDS):
            for value in table.values():
                for digest in (value,) if isinstance(value, str) else value:
                    self.assertIn(digest, recorded, "a recorded hash needs its Store package in WINDOWS_PACKAGE_RECORDS")

    def test_a_hash_is_never_both_tested_and_verified(self):
        tested = {h for v in win.TESTED_WINDOWS_SOURCE_BUILDS.values() for h in ((v,) if isinstance(v, str) else v)}
        verified = {h for v in win.CANARY_VERIFIED_WINDOWS_SOURCE_BUILDS.values() for h in ((v,) if isinstance(v, str) else v)}
        self.assertFalse(tested & verified)
        self.assertFalse(set(patch_app.TESTED_SOURCE_BUILDS) & set(patch_app.CANARY_VERIFIED_SOURCE_BUILDS))

    def test_the_recorded_builds_the_watcher_reads_match_the_tables(self):
        recorded = watch.recorded_windows()
        self.assertEqual(recorded[("26.930.3930.0", "x64")], "tested")
        self.assertEqual(recorded[("26.930.6422.0", "x64")], "verified")
        self.assertEqual(watch.recorded_macos()[("26.928.20755", "12246")], "tested")


class VerifiedTierTests(unittest.TestCase):
    """A build the canary verified is accepted without the flag; an unknown one still is not."""

    def test_windows(self):
        identity = win.SourceIdentity("1.0.0.0", "1.0.0.0", "ChatGPT", True, "ab" * 32)
        with mock.patch.object(win, "CANARY_VERIFIED_WINDOWS_SOURCE_BUILDS", {("1.0.0.0", "1.0.0.0"): ("ab" * 32,)}), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(win.approve_source(identity, False), "verified")
        self.assertIn("verified by the project's canary", out.getvalue())
        with mock.patch.object(win, "TESTED_WINDOWS_SOURCE_BUILDS", {("1.0.0.0", "1.0.0.0"): "ab" * 32}):
            self.assertEqual(win.approve_source(identity, False), "tested")
        with self.assertRaises(win.UntestedSourceError):
            win.approve_source(identity, False)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(win.approve_source(identity, True), "untested")

    def test_macos(self):
        with mock.patch.object(patch_app, "CANARY_VERIFIED_SOURCE_BUILDS", {("9.9", "99"): "cd" * 32}), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(patch_app.approve_source("9.9", "99", "cd" * 32, False), "verified")
        # The hash must match, not only the version and build.
        with self.assertRaises(patch_app.UntestedSourceError):
            patch_app.approve_source("9.9", "99", "cd" * 32, False)
        with mock.patch.object(patch_app, "TESTED_SOURCE_BUILDS", {("9.9", "99"): "ef" * 32}):
            self.assertEqual(patch_app.approve_source("9.9", "99", "ef" * 32, False), "tested")
            with self.assertRaises(patch_app.UntestedSourceError):
                patch_app.approve_source("9.9", "99", "00" * 32, False)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(patch_app.approve_source("9.9", "99", "00" * 32, True), "untested")


if __name__ == "__main__":
    unittest.main()
