"""All update experiments use freshly generated, isolated fake projects."""
from contextlib import ExitStack, contextmanager
import builtins
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import warnings
import zipfile

from app.file_safety import CONTENT_ROOTS, FILE_ROOTS, ContentSafetyError
from app.updater.package import UpdatePackage, yaml
from app.updater.transaction import (MANIFEST, PendingUpdateError, content_lock,
                                     recover_pending)
from app.updater.update_content import apply_update, main, newest_download
from app.updater.content_check import check_content
from app.tests.helpers import symlink_or_skip

SOURCE_ROOT = Path(__file__).absolute().parents[2]
# A validator script that would leave a mark if anything ever ran it.
TRAP = ("from pathlib import Path\n"
        "Path(__file__).resolve().parents[1].joinpath('VALIDATOR_RAN').write_text('ran')\n"
        "raise SystemExit(1)\n").encode()


def fixture_files(version="2026-10-03.2"):
    course = {"spec_version": 1, "content_version": version,
              "lab": {"name": "测试课题组", "fictional": True},
              "people": [{"id": "mentor", "name": "测试导师", "role": "测试", "initial": "测"}],
              "welcome": {"from": "mentor", "message": "测试欢迎内容"},
              "tracks": [{"id": "python", "label": "A", "title": "测试", "summary": "测试线",
                          "cards": ["first-card"]}]}
    card = {"id": "first-card", "type": "lesson", "title": "第一张测试卡", "subtitle": "仅用于测试",
            "minutes": 30, "requires": [], "verified": "2026-10-03", "resources": [],
            "questions": [{"id": "q" + str(i), "kind": "predict", "prompt": "题目", "answer": "答案"}
                          for i in range(1, 4)]}
    return {"content/course.yaml": yaml.safe_dump(course, allow_unicode=True).encode(),
            "content/cards/python/first-card.md":
                ("---\n" + yaml.safe_dump(card, allow_unicode=True) + "---\n## 讲解\n讲解\n## 怎么学\n学习\n").encode(),
            "tools/validate_content.py": (SOURCE_ROOT / "tools/validate_content.py").read_bytes(),
            "docs/CONTENT_SPEC.md": b"test specification",
            "HANDOFF.md": b"test handoff", "AGENTS.md": b"test instructions",
            ".agents/skills/learning-ui/SKILL.md": b"test design"}


def materialize(root, files):
    for allowed in CONTENT_ROOTS:
        if allowed not in FILE_ROOTS:
            (root / allowed).mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def write_zip(path, files, extra=(), roots=CONTENT_ROOTS):
    with zipfile.ZipFile(path, "w") as archive:
        for allowed in roots:
            if allowed not in FILE_ROOTS:
                archive.writestr(allowed + "/", b"")
        for name, data in files.items():
            archive.writestr(name, data)
        for name, data in extra:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                if isinstance(name, str):
                    # ZipInfo normalizes backslashes on Windows. Preserve the
                    # malformed spelling so the reader, not this fixture,
                    # decides whether an untrusted archive entry is safe.
                    entry = zipfile.ZipInfo(name)
                    entry.filename = entry.orig_filename = name
                else:
                    entry = name
                archive.writestr(entry, data)
    return path


def snapshot(root):
    result = {}
    for allowed in CONTENT_ROOTS:
        path = root / allowed
        if path.is_file():
            result[allowed] = path.read_bytes()
        else:
            for directory, unused, files in os.walk(path):
                for name in files:
                    item = Path(directory) / name
                    result[item.relative_to(root).as_posix()] = item.read_bytes()
    return result


@contextmanager
def monitor_parent(root):
    """Observe metadata operations too; unchanged sentinels alone are insufficient."""
    root = os.path.normcase(os.path.abspath(root))
    forbidden = [os.path.join(root, "progress"), os.path.join(root, "workspace")]
    calls = []
    descriptors = {}

    def record(path, action, kwargs=None):
        kwargs = kwargs or {}
        if isinstance(path, int):
            path = descriptors.get(path)
        if path is None:
            return
        path = os.fsdecode(path)
        if kwargs.get("dir_fd") is not None and not os.path.isabs(path):
            path = os.path.join(descriptors.get(kwargs["dir_fd"], root), path)
        absolute = os.path.normcase(os.path.abspath(path))
        calls.append((action, absolute))
        if any(absolute == item or absolute.startswith(item + os.sep) for item in forbidden):
            raise AssertionError("更新器访问了假保护目录：" + action + " " + absolute)
        if absolute == root and action in ("listdir", "scandir"):
            raise AssertionError("更新器扫描了项目根目录")

    with ExitStack() as stack:
        for owner, name in [(builtins, "open"), (io, "open")] + [
                (Path, name) for name in ("stat", "lstat", "readlink") if hasattr(Path, name)] + [
                (os, name) for name in ("open", "stat", "lstat", "scandir", "listdir", "readlink",
                                       "rename", "replace", "unlink", "remove", "rmdir", "mkdir", "chmod")]:
            original = getattr(owner, name)

            def wrapped(path, *args, _original=original, _name=name, _owner=owner, **kwargs):
                record(path, _name, kwargs)
                if _name in ("rename", "replace") and args:
                    record(args[0], _name + "_destination", kwargs)
                result = _original(path, *args, **kwargs)
                if _owner is os and _name == "open":
                    base = os.fsdecode(path)
                    if kwargs.get("dir_fd") is not None and not os.path.isabs(base):
                        base = os.path.join(descriptors.get(kwargs["dir_fd"], root), base)
                    descriptors[result] = os.path.abspath(base)
                return result

            stack.enter_context(patch.object(owner, name, wrapped))
        yield calls


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="learning-hub-updater-tests-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name).resolve()
        self.root = self.directory / "project"
        self.root.mkdir()
        self.old = fixture_files("2026-10-03.1")
        materialize(self.root, self.old)
        for name in ("progress", "workspace"):
            (self.root / name).mkdir()
            (self.root / name / "sentinel.txt").write_bytes(b"private-unchanged")
        self.files = fixture_files()
        self.package = self.directory / "learning-hub-update-test.zip"

    def zip(self, files=None, extra=()):
        return write_zip(self.package, self.files if files is None else files, extra)

    def assert_pristine(self):
        self.assertEqual(snapshot(self.root), self.old)
        for name in ("progress", "workspace"):
            self.assertEqual((self.root / name / "sentinel.txt").read_bytes(), b"private-unchanged")

    def test_valid_update_and_its_check_never_access_protected_paths(self):
        package = self.zip()
        with monitor_parent(self.root) as calls:
            result = apply_update(self.root, package)
        self.assertTrue(calls)
        self.assertEqual(result["new_version"], "2026-10-03.2")
        self.assertIn("0 个错误", result["validation_output"])
        self.assertEqual(snapshot(self.root), self.files)
        for private in ("progress", "workspace"):
            self.assertEqual((self.root / private / "sentinel.txt").read_bytes(), b"private-unchanged")

    def test_invalid_paths_reject_entire_package_before_mutation(self):
        for name in ("progress/x", "workspace/x", "app/x", "WORKLOG.md", "/content/x", "content/../x",
                     "content//x", "content/./x", "content\\x", "C:/content/x", "content/x\x01"):
            with self.subTest(name=name):
                package = self.zip(extra=[(name, b"invalid")])
                with zipfile.ZipFile(package) as archive:
                    self.assertIn(name, [entry.orig_filename for entry in archive.infolist()])
                with monitor_parent(self.root), self.assertRaises(ContentSafetyError):
                    apply_update(self.root, package)
                self.assert_pristine()

    def test_duplicate_case_unicode_and_file_directory_conflicts(self):
        cases = [[("content/course.yaml", b"duplicate")], [("content/COURSE.yaml", b"case")],
                 [("docs/Caf\u00e9.txt", b"one"), ("docs/Cafe\u0301.txt", b"two")],
                 [("docs/sub", b"file"), ("docs/sub/child", b"child")],
                 [("docs/Other/a", b"one"), ("docs/other/b", b"two")]]
        for extra in cases:
            with self.subTest(extra=extra), self.assertRaises(ContentSafetyError):
                UpdatePackage(self.zip(extra=extra))
        self.assert_pristine()

    def test_symlink_and_special_file_entries(self):
        for kind in (stat.S_IFLNK, stat.S_IFIFO, stat.S_IFSOCK):
            entry = zipfile.ZipInfo("docs/link")
            entry.create_system = 3
            entry.external_attr = (kind | 0o777) << 16
            with self.subTest(kind=kind), self.assertRaises(ContentSafetyError):
                UpdatePackage(self.zip(extra=[(entry, b"../workspace")]))

    def test_missing_course_and_complete_root(self):
        files = dict(self.files)
        del files["content/course.yaml"]
        with self.assertRaises(ContentSafetyError):
            UpdatePackage(self.zip(files))
        files = {name: data for name, data in self.files.items() if not name.startswith("data/")}
        roots = [root for root in CONTENT_ROOTS if root != "data"]
        with self.assertRaisesRegex(ContentSafetyError, "缺少完整路径：data"):
            UpdatePackage(write_zip(self.package, files, roots=roots))

    def test_package_with_only_the_course_leaves_other_roots_untouched(self):
        files = {name: data for name, data in self.files.items() if name.startswith(("content/", "data/"))}
        files.setdefault("data/README.txt", b"course data")
        with monitor_parent(self.root):
            result = apply_update(self.root, write_zip(self.package, files, roots=("content", "data")))
        self.assertEqual(result["new_version"], "2026-10-03.2")
        for name in ("AGENTS.md", "HANDOFF.md", "docs/CONTENT_SPEC.md", "tools/validate_content.py",
                     ".agents/skills/learning-ui/SKILL.md"):
            self.assertEqual((self.root / name).read_bytes(), self.old[name], name)
        self.assertEqual((self.root / "content/course.yaml").read_bytes(), self.files["content/course.yaml"])

    def test_course_only_package_in_a_project_without_author_files(self):
        for name in ("AGENTS.md", "HANDOFF.md"):
            (self.root / name).unlink()
        for name in ("docs", "tools", ".agents"):
            shutil.rmtree(self.root / name)
        files = {name: data for name, data in self.files.items() if name.startswith(("content/", "data/"))}
        files.setdefault("data/README.txt", b"course data")
        result = apply_update(self.root, write_zip(self.package, files, roots=("content", "data")))
        self.assertEqual(result["new_version"], "2026-10-03.2")
        self.assertFalse((self.root / "AGENTS.md").exists())
        self.assertFalse((self.root / "docs").exists())

    def test_package_validator_script_is_copied_but_never_run(self):
        files = dict(self.files)
        files["tools/validate_content.py"] = TRAP
        with monitor_parent(self.root):
            result = apply_update(self.root, self.zip(files))
        self.assertIn("0 个错误", result["validation_output"])
        self.assertEqual((self.root / "tools/validate_content.py").read_bytes(), TRAP)
        self.assertFalse((self.root / "VALIDATOR_RAN").exists())

    def test_check_names_the_broken_card_without_answers(self):
        files = dict(self.files)
        files["content/cards/python/first-card.md"] = files["content/cards/python/first-card.md"].replace(
            b"verified: '2026-10-03'", b"verified: soon")
        with monitor_parent(self.root), self.assertRaisesRegex(ContentSafetyError, "first-card：缺少有效的核实日期"):
            apply_update(self.root, self.zip(files))
        self.assert_pristine()

    def test_internal_track_and_card_references_rejected_before_access(self):
        for field, value in (("id", "../../workspace"), ("cards", ["../../progress/record"])):
            files = dict(self.files)
            course = yaml.safe_load(files["content/course.yaml"])
            course["tracks"][0][field] = value
            files["content/course.yaml"] = yaml.safe_dump(course).encode()
            package = self.zip(files)
            with monitor_parent(self.root), self.assertRaises(ContentSafetyError):
                apply_update(self.root, package)
            self.assert_pristine()

    def test_unsupported_course_format_is_rejected_before_replacement(self):
        for version in (99, 2, 0, True, "1", None):
            files = dict(self.files)
            course = yaml.safe_load(files["content/course.yaml"])
            course["spec_version"] = version
            files["content/course.yaml"] = yaml.safe_dump(course).encode()
            package = self.zip(files)
            with self.subTest(version=version), monitor_parent(self.root), self.assertRaisesRegex(ContentSafetyError, "先请 Codex 更新程序"):
                apply_update(self.root, package)
            self.assert_pristine()

    def test_internal_data_references_rejected_before_access(self):
        for value in ("../../../workspace/private", "/etc/passwd", "..\\workspace", "../../progress/sentinel.txt"):
            files = dict(self.files)
            text = files["content/cards/python/first-card.md"].decode()
            text = text.replace("---\n", "---\ndata:\n- file: " + value + "\n  description: test\n", 1)
            files["content/cards/python/first-card.md"] = text.encode()
            package = self.zip(files)
            with monitor_parent(self.root), self.assertRaises(ContentSafetyError):
                apply_update(self.root, package)
            self.assert_pristine()

    def test_existing_symlinks_are_rejected_without_following(self):
        symlink_or_skip(self, self.root / "docs/link", self.root / "workspace", directory=True)
        with monitor_parent(self.root), self.assertRaises(ContentSafetyError):
            apply_update(self.root, self.zip())
        (self.root / "docs/link").unlink()
        self.assert_pristine()

    def test_package_modules_are_never_imported(self):
        malicious = b"raise RuntimeError('THIS PACKAGE MODULE MUST NOT RUN')\n"
        package = self.zip(extra=[("tools/" + name + ".py", malicious)
                                  for name in ("yaml", "json", "datetime", "pathlib", "os", "sitecustomize")])
        with monitor_parent(self.root) as calls:
            result = apply_update(self.root, package)
        self.assertIn("0 个错误", result["validation_output"])
        opened = [path for action, path in calls if action == "open"]
        self.assertNotIn(os.path.normcase(str(self.root / "tools/yaml.py")), opened)

    def test_validation_failure_restores_all_seven_roots(self):
        files = dict(self.files)
        files["content/cards/python/first-card.md"] = files["content/cards/python/first-card.md"].replace(
            "## 怎么学".encode(), "## 错误标题".encode())
        package = self.zip(files)
        with monitor_parent(self.root), self.assertRaisesRegex(ContentSafetyError, "已恢复原版本"):
            apply_update(self.root, package)
        self.assert_pristine()
        self.assertFalse((self.root / "backups" / MANIFEST).exists())

    def test_failure_after_each_replacement_restores_full_original(self):
        package = self.zip()
        for allowed in CONTENT_ROOTS:
            def fault(step):
                if step == "after_replace:" + allowed:
                    raise OSError("injected replacement failure")
            with self.subTest(root=allowed), monitor_parent(self.root), self.assertRaises(OSError):
                apply_update(self.root, package, on_step=fault)
            self.assert_pristine()
            self.assertFalse((self.root / "backups" / MANIFEST).exists())

    def test_backup_retains_ten_complete_tool_owned_copies(self):
        package = self.zip()
        for unused in range(11):
            with monitor_parent(self.root):
                apply_update(self.root, package)
        backups = list((self.root / "backups").glob("content-*"))
        self.assertEqual(len(backups), 10)
        for backup in backups:
            self.assertEqual(snapshot(backup), self.files)

    def test_interrupted_process_blocks_readers_then_recovers(self):
        script = """
import os, sys
sys.path.insert(0, sys.argv[1])
from app.updater.update_content import apply_update
def crash(step):
    if step == 'after_replace:docs':
        os._exit(73)
apply_update(sys.argv[2], sys.argv[3], on_step=crash)
"""
        result = subprocess.run([sys.executable, "-I", "-c", script, str(SOURCE_ROOT),
                                 str(self.root), str(self.zip())], capture_output=True)
        self.assertEqual(result.returncode, 73, result.stderr.decode())
        with self.assertRaises(PendingUpdateError), content_lock(self.root):
            pass
        with monitor_parent(self.root):
            self.assertTrue(recover_pending(self.root))
        self.assert_pristine()
        with content_lock(self.root):
            pass
        self.assertFalse(recover_pending(self.root))

    def test_corrupt_transaction_fails_closed(self):
        (self.root / "backups").mkdir()
        (self.root / "backups" / MANIFEST).write_text('{"version":1,"backup":"../workspace"}', encoding='utf-8')
        with monitor_parent(self.root), self.assertRaises(PendingUpdateError):
            recover_pending(self.root)
        self.assert_pristine()

    def test_invalid_transaction_existed_shape_has_clear_recovery_error(self):
        (self.root / "backups").mkdir()
        manifest = {"version": 1, "backup": "content-20000101-000000-000000-00000001",
                    "stage": ".content-stage-test", "state": "prepared"}
        for value in (None, [], list(CONTENT_ROOTS), "invalid"):
            manifest["existed"] = value
            (self.root / "backups" / MANIFEST).write_text(json.dumps(manifest), encoding='utf-8')
            with self.subTest(value=value), monitor_parent(self.root), self.assertRaises(PendingUpdateError):
                recover_pending(self.root)
            self.assert_pristine()

    def test_invalid_backup_marker_is_ignored_after_successful_update(self):
        for index, value in enumerate((None, [], "invalid"), 1):
            backup = self.root / "backups" / ("content-20000101-000000-000000-0000000" + str(index))
            backup.mkdir(parents=True)
            (backup / ".learning-hub-backup.json").write_text(json.dumps(value), encoding='utf-8')
        with monitor_parent(self.root):
            result = apply_update(self.root, self.zip())
        self.assertEqual(result["new_version"], "2026-10-03.2")
        self.assertEqual(snapshot(self.root), self.files)
        self.assertFalse((self.root / "backups" / MANIFEST).exists())

    def test_existing_backups_symlink_is_rejected_before_following(self):
        symlink_or_skip(self, self.root / "backups", self.root / "workspace", directory=True)
        package = self.zip()
        with monitor_parent(self.root), self.assertRaises(ContentSafetyError):
            apply_update(self.root, package)
        (self.root / "backups").unlink()
        self.assert_pristine()

    def test_backup_failure_never_changes_current_content(self):
        from app.updater import transaction
        original = transaction._copy

        def fail_backup(source, destination):
            if source == self.root / "docs":
                raise OSError("backup disk full")
            original(source, destination)
        with patch.object(transaction, "_copy", fail_backup), self.assertRaises(OSError):
            apply_update(self.root, self.zip())
        self.assert_pristine()
        self.assertFalse((self.root / "backups" / MANIFEST).exists())

    def test_failed_rollback_preserves_transaction_for_retry(self):
        from app.updater import transaction
        original = transaction._copy

        def fail_restore(source, destination):
            if "backups" in source.parts and destination == self.root / "docs":
                raise OSError("restore temporarily unavailable")
            original(source, destination)

        def fail_update(step):
            if step == "after_replace:docs":
                raise OSError("replace failure")
        with patch.object(transaction, "_copy", fail_restore), self.assertRaises(PendingUpdateError):
            apply_update(self.root, self.zip(), on_step=fail_update)
        self.assertTrue((self.root / "backups" / MANIFEST).exists())
        with monitor_parent(self.root):
            self.assertTrue(recover_pending(self.root))
        self.assert_pristine()

    def test_interruption_after_commit_keeps_valid_new_content(self):
        script = """
import os, sys
sys.path.insert(0, sys.argv[1])
from app.updater.update_content import apply_update
def crash(step):
    if step == 'committed':
        os._exit(73)
apply_update(sys.argv[2], sys.argv[3], on_step=crash)
"""
        result = subprocess.run([sys.executable, "-I", "-c", script, str(SOURCE_ROOT),
                                 str(self.root), str(self.zip())], capture_output=True)
        self.assertEqual(result.returncode, 73, result.stderr.decode())
        with monitor_parent(self.root):
            self.assertTrue(recover_pending(self.root))
        self.assertEqual(snapshot(self.root), self.files)

    def test_check_rejects_paths_outside_content_before_touching_them(self):
        course_path = self.root / "content/course.yaml"
        course = yaml.safe_load(course_path.read_bytes())
        course["tracks"][0]["id"] = "../../workspace"
        course_path.write_text(yaml.safe_dump(course), encoding="utf-8")
        with monitor_parent(self.root):
            report = check_content(self.root)
        self.assertEqual(report["code"], 1)
        self.assertIn("线路 id 不正确", report["output"])
        self.assertEqual((self.root / "workspace/sentinel.txt").read_bytes(), b"private-unchanged")

    def test_real_current_content_in_temporary_project(self):
        # Only the seven approved source roots are read, never source progress
        # or workspace. This checks the real published package without updating it.
        current = snapshot(SOURCE_ROOT)
        # Roots the package carries are replaced as a whole; any it leaves out stay as they were.
        carried = {root for root in CONTENT_ROOTS
                   if any(name == root or name.startswith(root + "/") for name in current)}
        package = write_zip(self.package, current, roots=[r for r in CONTENT_ROOTS if r in carried])
        with monitor_parent(self.root):
            result = apply_update(self.root, package)
        self.assertIn("0 个错误", result["validation_output"])
        inside = lambda name: any(name == root or name.startswith(root + "/") for root in carried)
        after = snapshot(self.root)
        self.assertEqual({k: v for k, v in after.items() if inside(k)}, current)
        self.assertEqual({k: v for k, v in after.items() if not inside(k)},
                         {k: v for k, v in self.old.items() if not inside(k)})

    def test_cancel_does_not_replace_content(self):
        lines = []
        with monitor_parent(self.root):
            result = main(["--root", str(self.root), str(self.zip())], input_fn=lambda question: "n", output=lines.append)
        self.assertEqual(result, 0)
        self.assertTrue(any("已取消" in line for line in lines))
        self.assert_pristine()

    def test_downloads_search_uses_top_level_mtime_only(self):
        directory = self.directory / "Downloads"
        directory.mkdir()
        older = directory / "learning-hub-update-old.zip"
        newer = directory / "learning-hub-update-new.zip"
        older.touch()
        newer.touch()
        os.utime(older, (10, 10))
        os.utime(newer, (20, 20))
        nested = directory / "nested"
        nested.mkdir()
        (nested / "learning-hub-update-ignored.zip").touch()
        self.assertEqual(newest_download(directory), newer)

    def test_launcher_is_ascii(self):
        # The launcher itself is an approved program entry, never content input.
        (SOURCE_ROOT / "更新内容.command").read_bytes().decode("ascii")

    @unittest.skipIf(sys.platform == "win32", "Mac .command entry requires /bin/sh; Python updater tests run on Windows")
    def test_command_launcher_runs_update_against_temporary_root(self):
        result = subprocess.run(["/bin/sh", str(SOURCE_ROOT / "更新内容.command"),
                                 "--root", str(self.root), str(self.zip())],
                                input="y\n\n", capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("更新完成。刷新浏览器页面即可。", result.stdout)
        self.assertIn("请按 Enter 关闭这个窗口。", result.stdout)
        self.assertEqual(snapshot(self.root), self.files)

    def test_content_reader_waits_for_updater_exclusive_lock(self):
        script = """
import sys
sys.path.insert(0, sys.argv[1])
from app.updater.transaction import content_lock
with content_lock(sys.argv[2], exclusive=True):
    print('locked', flush=True)
    input()
"""
        process = subprocess.Popen([sys.executable, "-I", "-c", script, str(SOURCE_ROOT), str(self.root)],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: process.kill() if process.poll() is None else None)
        self.assertEqual(process.stdout.readline().strip(), "locked")
        acquired = threading.Event()

        def read():
            with content_lock(self.root):
                acquired.set()
        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        self.assertFalse(acquired.wait(0.05))
        process.communicate("\n", timeout=5)
        thread.join(timeout=5)
        self.assertTrue(acquired.is_set())


if __name__ == "__main__":
    unittest.main()


class DesktopUpdaterTests(unittest.TestCase):
    setUp = UpdaterTests.setUp
    zip = UpdaterTests.zip
    assert_pristine = UpdaterTests.assert_pristine

    def worker(self, package, fingerprint):
        lines = []
        code = main([str(package), '--root', str(self.root), '--non-interactive',
                     '--expected-sha256', fingerprint, '--expected-content-version', '2026-10-03.1'], output=lines.append)
        return code, [json.loads(line) for line in lines]

    def test_full_inspection_checks_in_isolated_root_without_private_access(self):
        import hashlib
        from app.updater.update_content import inspect_update
        package = self.zip()
        with monitor_parent(self.root) as calls:
            result = inspect_update(self.root, package)
        self.assertEqual(result['fingerprint'], hashlib.sha256(package.read_bytes()).hexdigest())
        self.assertEqual((result['old_version'], result['new_version']), ('2026-10-03.1', '2026-10-03.2'))
        self.assertIn('0 个错误', result['validation_output'])
        project = os.path.normcase(str(self.root)) + os.sep
        # The new course is read in the disposable copy; only the current version number is read here.
        self.assertFalse([path for action, path in calls if action == 'open' and path.startswith(project + os.path.join('content', 'cards'))])
        self.assert_pristine()
        self.assertEqual(list((self.root / 'backups').iterdir()), [self.root / 'backups/.content-update.lock'])

    def test_failed_full_inspection_keeps_current_roots_unchanged(self):
        from app.updater.update_content import inspect_update
        files = dict(self.files)
        files['content/cards/python/first-card.md'] = files['content/cards/python/first-card.md'].replace('## 怎么学'.encode(), '## 坏标题'.encode())
        with monitor_parent(self.root), self.assertRaisesRegex(ContentSafetyError, '现有内容保持不动'):
            inspect_update(self.root, self.zip(files))
        self.assert_pristine()

    def test_inspection_never_runs_the_package_validator(self):
        from app.updater.update_content import inspect_update
        files = dict(self.files); files['tools/validate_content.py'] = TRAP
        with monitor_parent(self.root):
            result = inspect_update(self.root, self.zip(files))
        self.assertIn('0 个错误', result['validation_output'])
        self.assertFalse((self.root / 'VALIDATOR_RAN').exists())
        self.assert_pristine()

    def test_expected_package_fingerprint_rechecked_before_replacement(self):
        package = self.zip()
        fingerprint = UpdatePackage(package).fingerprint
        self.zip(fixture_files('2026-10-03.3'))
        with monitor_parent(self.root), self.assertRaisesRegex(ContentSafetyError, '检查后发生变化'):
            apply_update(self.root, package, expected_sha256=fingerprint)
        self.assert_pristine()

    def test_worker_requires_confirmation_fingerprint(self):
        code, events = self.worker(self.zip(), '')
        self.assertEqual(code, 1)
        self.assertEqual(events[-1]['type'], 'error')
        self.assertFalse(events[-1]['restored'])
        self.assert_pristine()

    def test_worker_emits_progress_and_result_without_prompts(self):
        package = self.zip()
        with monitor_parent(self.root):
            code, events = self.worker(package, UpdatePackage(package).fingerprint)
        self.assertEqual(code, 0)
        self.assertTrue(any(item['type'] == 'progress' and item['step'] == 'before_validate' for item in events))
        self.assertEqual(events[-1]['type'], 'result')
        self.assertEqual(events[-1]['result']['new_version'], '2026-10-03.2')
        self.assertEqual(snapshot(self.root), self.files)

    def test_worker_validation_failure_reports_restored_only_after_rollback(self):
        files = dict(self.files)
        files['content/cards/python/first-card.md'] = files['content/cards/python/first-card.md'].replace('## 怎么学'.encode(), '## 坏标题'.encode())
        package = self.zip(files)
        with monitor_parent(self.root):
            code, events = self.worker(package, UpdatePackage(package).fingerprint)
        self.assertEqual(code, 1)
        self.assertTrue(events[-1]['restored'])
        self.assert_pristine()

    def test_worker_failed_rollback_never_claims_restored(self):
        package = self.zip()
        with patch('app.updater.update_content.check_content', return_value={'code': 1, 'output': 'bad'}), patch('app.updater.transaction.rollback_locked', side_effect=OSError('restore failure')):
            code, events = self.worker(package, UpdatePackage(package).fingerprint)
        self.assertEqual(code, 1)
        self.assertFalse(events[-1]['restored'])
        self.assertIn('自动恢复未完成', events[-1]['message'])
        self.assertTrue((self.root / 'backups' / MANIFEST).exists())
        recover_pending(self.root)
        self.assert_pristine()

    def test_worker_failed_old_transaction_recovery_never_claims_restored(self):
        package = self.zip()
        with patch('app.updater.update_content.recover_locked', side_effect=OSError('recovery failed')):
            code, events = self.worker(package, UpdatePackage(package).fingerprint)
        self.assertEqual(code, 1)
        self.assertFalse(events[-1]['restored'])

    def test_worker_postcommit_cleanup_failure_never_claims_old_content_restored(self):
        package = self.zip()
        with patch('app.updater.transaction.trim_backups', side_effect=OSError('cleanup failed')):
            code, events = self.worker(package, UpdatePackage(package).fingerprint)
        self.assertEqual(code, 1)
        self.assertFalse(events[-1]['restored'])
        self.assertEqual(snapshot(self.root), self.files)

    def test_noninteractive_command_protocol_is_valid_utf8_json(self):
        package = self.zip()
        completed = subprocess.run([sys.executable, '-B', '-m', 'app.updater.update_content', str(package),
                                    '--root', str(self.root), '--non-interactive', '--expected-sha256', UpdatePackage(package).fingerprint,
                                    '--expected-content-version', '2026-10-03.1'],
                                   capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        events = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(events[-1]['type'], 'result')
        self.assertEqual(snapshot(self.root), self.files)

    def test_windows_reserved_names_rejected_before_mutation(self):
        for name in ('docs/CON.txt', 'data/AUX', 'docs/trailing.', 'docs/trailing ', 'docs/dir/NUL.csv'):
            with self.subTest(name=name), monitor_parent(self.root), self.assertRaises(ContentSafetyError):
                apply_update(self.root, self.zip(extra=[(name, b'unsafe')]))
            self.assert_pristine()


    def test_confirmed_old_version_rechecked_under_apply_lock_after_other_update(self):
        candidate = self.zip()
        fingerprint = UpdatePackage(candidate).fingerprint
        later_files = fixture_files('2026-10-03.9')
        later = write_zip(self.directory / 'learning-hub-update-later.zip', later_files)
        apply_update(self.root, later)
        with monitor_parent(self.root), self.assertRaisesRegex(ContentSafetyError, '课程版本在检查后发生变化'):
            apply_update(self.root, candidate, expected_sha256=fingerprint,
                         expected_content_version='2026-10-03.1')
        self.assertEqual(snapshot(self.root), later_files)

    def test_worker_rejects_content_version_change_after_confirmation(self):
        candidate = self.zip()
        fingerprint = UpdatePackage(candidate).fingerprint
        later_files = fixture_files('2026-10-03.9')
        later = write_zip(self.directory / 'learning-hub-update-later.zip', later_files)
        apply_update(self.root, later)
        with monitor_parent(self.root):
            code, events = self.worker(candidate, fingerprint)
        self.assertEqual(code, 1)
        self.assertIn('课程版本在检查后发生变化', events[-1]['message'])
        self.assertEqual(snapshot(self.root), later_files)

    def test_worker_missing_confirmed_course_version_is_rejected(self):
        candidate = self.zip(); lines = []
        code = main([str(candidate), '--root', str(self.root), '--non-interactive',
                     '--expected-sha256', UpdatePackage(candidate).fingerprint], output=lines.append)
        self.assertEqual(code, 1)
        self.assertIn('当前课程版本', json.loads(lines[-1])['message'])
        self.assert_pristine()
