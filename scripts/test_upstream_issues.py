"""Tests for the tracking-issue sync, against an in-memory stand-in for the gh CLI."""
import json
import unittest

import upstream_issues as issues
import upstream_watch as watch


class FakeGh:
    """Just enough of `gh issue/label` to keep issues in a list."""

    def __init__(self):
        self.issues = []
        self.comments = []
        self.closed = []
        self.labels_created = 0
        self.fail_label = False
        self.next_number = 1

    def __call__(self, arguments):
        group, action = arguments[0], arguments[1]
        option = lambda name: arguments[arguments.index(name) + 1] if name in arguments else None  # noqa: E731
        if group == "label" and action == "create":
            self.labels_created += 1
            if self.fail_label:
                raise RuntimeError("already exists")
            return ""
        if (group, action) == ("issue", "list"):
            return json.dumps([dict(issue) for issue in self.issues if issue["number"] not in self.closed])
        if (group, action) == ("issue", "create"):
            assert option("--label") == issues.LABEL
            self.issues.append({"number": self.next_number, "title": option("--title"), "body": option("--body")})
            self.next_number += 1
            return ""
        number = int(arguments[2]) if len(arguments) > 2 and arguments[2].isdigit() else None
        issue = next((i for i in self.issues if i["number"] == number), None)
        if (group, action) == ("issue", "edit"):
            if option("--title"):
                issue["title"] = option("--title")
            if option("--body"):
                issue["body"] = option("--body")
            return ""
        if (group, action) == ("issue", "comment"):
            self.comments.append((number, option("--body")))
            return ""
        if (group, action) == ("issue", "close"):
            self.closed.append(number)
            self.comments.append((number, option("--comment")))
            return ""
        raise AssertionError(f"unexpected gh call: {arguments}")


def report(macos_level=None, windows_level=None, windows_version="26.930.7945.0", mac_version="26.930.61225", mac_build="13232"):
    return {
        "macos": {
            "latest": {"version": mac_version, "build": mac_build, "published": "Tue, 06 Oct 2026", "url": "u"},
            "level": macos_level,
            "builds_not_recorded": 6,
        },
        "windows": {
            "x64": {"latest": {"full_name": f"OpenAI.Codex_{windows_version}_x64__x", "version": windows_version, "architecture": "x64"}, "level": windows_level},
        },
        "needs_attention": [],
        "errors": [],
    }


class SyncTests(unittest.TestCase):
    def test_an_unrecorded_build_opens_one_issue_per_platform(self):
        gh = FakeGh()
        done = issues.sync(report(), gh)
        self.assertEqual(len(gh.issues), 2)
        self.assertEqual(len(done), 2)
        titles = sorted(i["title"] for i in gh.issues)
        self.assertEqual(titles[0], "[Windows x64] Official build 26.930.7945.0 is not verified yet")
        self.assertEqual(titles[1], "[macOS] Official build 26.930.61225 (build 13232) is not verified yet")
        self.assertIn(issues.marker("macos"), next(i for i in gh.issues if "macOS" in i["title"])["body"])
        self.assertIn("6 published builds are newer", next(i for i in gh.issues if "macOS" in i["title"])["body"])

    def test_running_again_with_nothing_new_changes_nothing(self):
        gh = FakeGh()
        issues.sync(report(), gh)
        done = issues.sync(report(), gh)
        self.assertEqual(done, [])
        self.assertEqual(len(gh.issues), 2)
        self.assertEqual(gh.comments, [])

    def test_a_newer_build_updates_the_title_and_comments_once(self):
        gh = FakeGh()
        issues.sync(report(), gh)
        issues.sync(report(mac_version="26.930.71234", mac_build="13300"), gh)
        mac = next(i for i in gh.issues if "macOS" in i["title"])
        self.assertIn("26.930.71234", mac["title"])
        self.assertIn("26.930.71234", mac["body"])
        self.assertEqual(len(gh.comments), 1)
        self.assertIn("A newer official build is out", gh.comments[0][1])

    def test_a_recorded_newest_build_closes_the_issue(self):
        gh = FakeGh()
        issues.sync(report(), gh)
        done = issues.sync(report(macos_level="verified", windows_level="tested"), gh)
        self.assertEqual(len(done), 2)
        self.assertEqual(sorted(gh.closed), [1, 2])
        self.assertIn("is now recorded (verified)", gh.comments[0][1])

    def test_a_recorded_build_with_no_issue_does_nothing(self):
        gh = FakeGh()
        self.assertEqual(issues.sync(report(macos_level="tested", windows_level="tested"), gh), [])
        self.assertEqual(gh.issues, [])

    def test_the_label_already_existing_is_not_an_error(self):
        gh = FakeGh()
        gh.fail_label = True
        issues.sync(report(), gh)
        self.assertEqual(len(gh.issues), 2)

    def test_the_canary_section_survives_a_refresh(self):
        gh = FakeGh()
        issues.sync(report(), gh)
        issues.apply_canary([{"platform": "macos", "status": "fail", "message": "expected 17, found 16",
                              "asar": "ab" * 32, "app_version": "26.930.61225", "build": "13232", "boot": "skipped",
                              "package": "", "run_url": "https://example.test/run/1"}], gh)
        issues.sync(report(mac_version="26.930.71234", mac_build="13300"), gh)
        mac = next(i for i in gh.issues if "macOS" in i["title"])
        self.assertIn("expected 17, found 16", mac["body"])


class CanaryTests(unittest.TestCase):
    PASS = {"platform": "windows-x64", "status": "pass", "message": "", "asar": "cd" * 32, "app_version": "26.930.61225",
            "build": "13232", "package": "26.930.7945.0", "boot": "ok", "boot_detail": "", "run_url": "https://example.test/run/2"}

    def test_the_result_goes_into_the_existing_issue(self):
        gh = FakeGh()
        issues.sync(report(), gh)
        done = issues.apply_canary([self.PASS], gh)
        win = next(i for i in gh.issues if "Windows x64" in i["title"])
        self.assertIn("**passed**", win["body"])
        self.assertIn("the patched app booted", win["body"])
        self.assertIn("record_build.py", win["body"])
        self.assertIn("[run](https://example.test/run/2)", win["body"])
        self.assertEqual(len(done), 1)
        # Unchanged result: no edit.
        self.assertEqual(issues.apply_canary([self.PASS], gh), [])

    def test_a_failing_canary_with_no_issue_opens_one(self):
        gh = FakeGh()
        done = issues.apply_canary([dict(self.PASS, status="fail", message="expected 17 found 16")], gh)
        self.assertEqual(len(gh.issues), 1)
        self.assertIn("cannot patch the newest official build", gh.issues[0]["title"])
        self.assertIn("expected 17 found 16", gh.issues[0]["body"])
        self.assertEqual(len(done), 1)

    def test_a_passing_canary_with_no_issue_stays_quiet(self):
        gh = FakeGh()
        self.assertEqual(issues.apply_canary([self.PASS], gh), [])
        self.assertEqual(gh.issues, [])

    def test_unknown_platforms_are_ignored(self):
        gh = FakeGh()
        self.assertEqual(issues.apply_canary([dict(self.PASS, platform="linux", status="fail")], gh), [])


class EntriesTests(unittest.TestCase):
    def test_entries_come_from_the_real_report_shape(self):
        built = watch.evaluate(
            watch.parse_appcast(
                '<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle"><channel><item>'
                "<pubDate>x</pubDate><sparkle:version>9</sparkle:version><sparkle:shortVersionString>1.2</sparkle:shortVersionString>"
                '<enclosure url="u"/></item></channel></rss>'
            ),
            watch.parse_store_catalog(
                json.dumps({"Products": [{"DisplaySkuAvailabilities": [{"Sku": {"Properties": {"Packages": [
                    {"PackageFullName": "OpenAI.Codex_1.2.3.4_arm64__x"}]}}}]}]})
            ),
            {},
            {},
        )
        found = issues.entries(built)
        self.assertEqual(sorted(found), ["macos", "windows-arm64"])
        self.assertEqual(found["macos"]["version"], "1.2 (build 9)")
        self.assertEqual(found["windows-arm64"]["version"], "1.2.3.4")


if __name__ == "__main__":
    unittest.main()
