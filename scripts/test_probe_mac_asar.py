"""Tests for the macOS asar probe's file placement (no download, no extraction)."""
import plistlib
import tempfile
import unittest
import zipfile
from pathlib import Path

import probe_mac_asar as probe


def make_zip(path: Path, with_asar=True):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(probe.BUNDLE_PLIST, plistlib.dumps({"CFBundleShortVersionString": "9.9.9", "CFBundleVersion": "99"}))
        if with_asar:
            archive.writestr(probe.BUNDLE_RESOURCES + "app.asar", b"archive")
            archive.writestr(probe.BUNDLE_RESOURCES + "app.asar.unpacked/node_modules/a/b.node", b"native")
        archive.writestr(probe.BUNDLE_RESOURCES + "codex-cli/CodexCLI.app/Contents/Info.plist", b"not wanted")
        archive.writestr("ChatGPT.app/Contents/MacOS/ChatGPT", b"not wanted")


class PlacementTests(unittest.TestCase):
    def test_only_the_archive_its_unpacked_modules_and_the_plist_are_placed(self):
        with tempfile.TemporaryDirectory() as directory:
            zip_path = Path(directory) / "app.zip"
            make_zip(zip_path)
            destination = Path(directory) / "out"
            destination.mkdir()
            info = probe.place_files(zip_path, destination)
            self.assertEqual(info["CFBundleShortVersionString"], "9.9.9")
            self.assertEqual((destination / "app.asar").read_bytes(), b"archive")
            self.assertEqual((destination / "app.asar.unpacked" / "node_modules" / "a" / "b.node").read_bytes(), b"native")
            placed = sorted(path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file())
            self.assertEqual(placed, ["Info.plist", "app.asar", "app.asar.unpacked/node_modules/a/b.node"])

    def test_a_zip_without_the_archive_is_refused_by_name(self):
        with tempfile.TemporaryDirectory() as directory:
            zip_path = Path(directory) / "app.zip"
            make_zip(zip_path, with_asar=False)
            with self.assertRaises(SystemExit) as caught:
                probe.place_files(zip_path, Path(directory))
            self.assertIn("app.asar", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
