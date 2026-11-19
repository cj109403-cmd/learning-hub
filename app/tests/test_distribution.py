import os
import builtins
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from app.distribution import build_share as share
from app.tests.helpers import symlink_or_skip


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="learning-hub-share-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for name in set(share.TREES + share.WINDOWS_TREES):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        for name in set(share.FILES + share.WINDOWS_FILES):
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture: " + name, encoding="utf-8")
        (self.root / "app/static/index.html").write_text("welcome", encoding="utf-8")

    def test_allowlist_never_inspects_private_roots(self):
        for name in ("progress", "workspace", "logs", "backups", ".git"):
            (self.root / name).mkdir()
            (self.root / name / "private.txt").write_text("secret", encoding="utf-8")
        original_stat = Path.lstat
        original_scandir = os.scandir
        original_os_stat = os.stat
        original_open = builtins.open
        visited = []
        def inspect(path, *args, **kwargs):
            if isinstance(path, int):
                return
            relative = Path(path).relative_to(self.root) if Path(path).is_relative_to(self.root) else None
            if relative and relative.parts and relative.parts[0] in share.EXCLUDE_NAMES:
                self.fail("inspected private path: " + str(relative))
            visited.append(str(relative))
        def lstat(path, *args, **kwargs):
            inspect(path); return original_stat(path, *args, **kwargs)
        def scandir(path):
            self.assertNotEqual(Path(path), self.root, "must never enumerate project root")
            inspect(path); return original_scandir(path)
        def os_stat(path, *args, **kwargs):
            inspect(path); return original_os_stat(path, *args, **kwargs)
        def open_file(path, *args, **kwargs):
            inspect(path); return original_open(path, *args, **kwargs)
        output = self.root / "app/distribution/dist/share.zip"
        with mock.patch.object(Path, "lstat", lstat), mock.patch.object(os, "scandir", scandir), \
             mock.patch.object(os, "stat", os_stat), mock.patch.object(builtins, "open", open_file):
            for platform in ("all", "windows"):
                with self.subTest(platform=platform):
                    share.build_share(self.root, output, platform)
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
        self.assertTrue(visited)
        self.assertIn("learning-hub/app/static/index.html", names)
        self.assertIn("learning-hub/app/THIRD_PARTY_NOTICES.txt", names)
        self.assertFalse(any("private" in name or "WORKLOG" in name or "QA.md" in name for name in names))

    def test_nested_build_cache_and_screenshots_excluded(self):
        for name in ("app/macos/build", "app/tests/__pycache__", "app/tests/screenshots"):
            folder = self.root / name; folder.mkdir()
            (folder / "private.tmp").write_text("excluded", encoding="utf-8")
        files = [str(p.relative_to(self.root)) for p in share.safe_files(self.root)]
        self.assertFalse(any("private.tmp" in name for name in files))

    def test_symlink_in_allowed_tree_is_rejected(self):
        symlink_or_skip(self, self.root / "app/static/outside.js", "../../../../outside")
        with self.assertRaisesRegex(ValueError, "链接"):
            list(share.safe_files(self.root))

    def test_parent_link_is_rejected_before_reading_files(self):
        # app is a parent of a fixed FILE entry and must be checked too.
        moved = self.root / "other-app"
        (self.root / "app").rename(moved)
        symlink_or_skip(self, self.root / "app", moved, directory=True)
        with self.assertRaisesRegex(ValueError, "链接"):
            list(share.safe_files(self.root))

    def test_output_cannot_be_in_private_or_source_location(self):
        for target in ("progress/exports/share.zip", "workspace/share.zip", "content/share.zip"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                share.build_share(self.root, self.root / target)

    def test_course_extras_travel_with_the_course_when_present(self):
        output = self.root / "app/distribution/dist/share.zip"
        for platform in ("all", "windows"):
            share.build_share(self.root, output, platform)
            with zipfile.ZipFile(output) as archive:
                self.assertNotIn("learning-hub/app/course_extras.json", archive.namelist())
            (self.root / "app/course_extras.json").write_text('{"hints_from": "mentor"}', encoding="utf-8")
            share.build_share(self.root, output, platform)
            with zipfile.ZipFile(output) as archive:
                self.assertIn("learning-hub/app/course_extras.json", archive.namelist())
            (self.root / "app/course_extras.json").unlink()

    def test_missing_required_document_rejects_whole_package(self):
        (self.root / "app/ARCHITECTURE.md").unlink()
        output = self.root / "app/distribution/dist/share.zip"
        with self.assertRaises(FileNotFoundError):
            share.build_share(self.root, output)
        self.assertFalse(output.exists())

    def test_windows_package_omits_and_never_inspects_developer_material(self):
        excluded = ("HANDOFF.md", "AGENTS.md", "docs", ".agents", "app/tests",
                    "app/macos", "app/README.md", "app/ARCHITECTURE.md")
        original_lstat = Path.lstat

        def lstat(path, *args, **kwargs):
            relative = path.relative_to(self.root).as_posix() if path.is_relative_to(self.root) else ""
            self.assertFalse(any(relative == p or relative.startswith(p + "/") for p in excluded), relative)
            return original_lstat(path, *args, **kwargs)

        output = self.root / "app/distribution/dist/windows.zip"
        with mock.patch.object(Path, "lstat", lstat):
            count = share.build_share(self.root, output, "windows")
        with zipfile.ZipFile(output) as archive:
            names = archive.namelist()
            self.assertEqual(count, len(names))
            self.assertEqual(len(names), len(set(names)))
            self.assertIn("learning-hub/app/assets/icons/orange/learning-hub.ico", names)
            self.assertIn("learning-hub/tools/validate_content.py", names)
            self.assertFalse(any(n == "learning-hub/" + p or n.startswith("learning-hub/" + p + "/")
                                 for n in names for p in excluded if p != "app/README.md"))
            self.assertEqual(archive.read("learning-hub/app/README.md"), archive.read("learning-hub/安装说明.txt"))

    def test_windows_instructions_and_entrypoint_are_ready_for_notepad_and_cmd(self):
        (self.root / "app/distribution/windows/installation.txt").write_text("安装说明\n保留记录\n", encoding="utf-8")
        (self.root / "app/distribution/windows/install.cmd").write_text("@echo off\nexit /b 0\n", encoding="ascii")
        output = self.root / "app/distribution/dist/windows.zip"
        share.build_share(self.root, output, "windows")
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read("learning-hub/安装说明.txt"), "安装说明\r\n保留记录\r\n".encode("utf-8-sig"))
            self.assertEqual(archive.read("learning-hub/安装学习台.cmd"), b"@echo off\r\nexit /b 0\r\n")

    def test_windows_package_rejects_missing_icon_and_linked_instructions(self):
        output = self.root / "app/distribution/dist/windows.zip"
        icon = self.root / "app/assets/icons/orange/learning-hub.ico"
        icon.unlink()
        with self.assertRaises(FileNotFoundError):
            share.build_share(self.root, output, "windows")
        self.assertFalse(output.exists())
        icon.write_bytes(b"icon")
        guide = self.root / "app/distribution/windows/installation.txt"
        guide.unlink()
        symlink_or_skip(self, guide, "../../../../outside")
        with self.assertRaisesRegex(ValueError, "链接"):
            share.build_share(self.root, output, "windows")
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
