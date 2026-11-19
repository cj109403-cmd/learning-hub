import hashlib
import json
import multiprocessing
import os
import signal
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.server import progress as storage
from app.server.migrations import MigrationError, new_progress
from app.server.progress import ConflictError, ProgressStore, StorageError
from app.tests.helpers import symlink_or_skip


def _concurrent_writer(directory, revision, gate, queue, marker):
    store = ProgressStore(Path(directory))
    store.startup()
    gate.wait(10)
    try:
        saved, _ = store.mutate(revision, lambda value: value.update(writer=marker))
        queue.put(("ok", saved["progress"]["writer"]))
    except ConflictError as exc:
        queue.put(("conflict", exc.status))


def _crash_writer(directory, revision, after_replace, reached):
    store = ProgressStore(Path(directory))
    store.startup()
    original = storage.os.replace
    def terminate(source, target):
        if str(target) == str(store.path):
            if after_replace:
                original(source, target)
            reached.set()
            # The parent kills this process at the replacement boundary using
            # TerminateProcess on Windows or SIGKILL on POSIX, without cleanup.
            time.sleep(30)
            raise RuntimeError("writer was not killed at the replacement boundary")
        return original(source, target)
    storage.os.replace = terminate
    store.mutate(revision, lambda value: value.update(crash_marker="whole new value"))


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="learning-hub-record-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / "records"
        self.store = ProgressStore(self.directory)
        self.initial = self.store.startup()

    def write_raw(self, raw):
        self.store.path.write_bytes(raw)

    def test_initial_record_and_settings_use_confirmed_defaults(self):
        self.assertFalse(self.initial["read_only"])
        self.assertEqual(self.initial["progress"], new_progress())
        self.assertNotIn("known_cards", self.initial["progress"])
        self.assertEqual(self.initial["revision"], hashlib.sha256(self.store.path.read_bytes()).hexdigest())
        self.assertEqual(self.store.get_settings(), storage.DEFAULT_SETTINGS)
        self.assertFalse(self.store.settings_path.exists())
        self.assertEqual(len(list(self.store.backups.glob("*.json"))), 1)

    def test_callback_result_and_unknown_records_survive(self):
        def change(value):
            value["unknown"] = {"metadata": ["保留"]}
            value["cards"]["retired"] = {"status": "done", "summary": "保留总结", "ui": {"learn_completed": True}}
            return {"result": "业务结果"}
        snapshot, result = self.store.mutate(self.initial["revision"], change)
        self.assertEqual(result, {"result": "业务结果"})
        self.assertNotEqual(snapshot["revision"], self.initial["revision"])
        restarted = ProgressStore(self.directory).startup()
        self.assertEqual(snapshot["progress"], restarted["progress"])
        snapshot["progress"]["unknown"].clear()
        self.assertTrue(self.store.read()["progress"]["unknown"])

    def test_exact_original_bytes_backed_up_before_migration(self):
        raw = (Path(__file__).parent / "fixtures" / "progress_versions" / "v1.json").read_bytes()
        self.write_raw(raw)
        upgraded = ProgressStore(self.directory).startup()
        self.assertFalse(upgraded["read_only"])
        self.assertEqual(upgraded["progress"]["spec_version"], 2)
        self.assertTrue(any(path.read_bytes() == raw for path in self.store.backups.glob("*.json")))
        self.assertTrue(any("升级" in notice for notice in upgraded["notices"]))

    def test_failed_migration_keeps_v1_original_and_backup(self):
        raw = b'{"spec_version":1,"cards":{},"events":[],"unknown":"keep"}\n'
        self.write_raw(raw)
        with patch("app.server.progress.migrate", side_effect=MigrationError("模拟迁移失败")):
            stopped = ProgressStore(self.directory).startup()
        self.assertTrue(stopped["read_only"])
        self.assertEqual(self.store.path.read_bytes(), raw)
        self.assertTrue(any(path.read_bytes() == raw for path in self.store.backups.glob("*.json")))

    def test_atomic_replace_failure_keeps_previous_json_and_cleans_temp(self):
        before = self.store.path.read_bytes()
        with patch("app.server.progress.os.replace", side_effect=OSError("模拟替换失败")):
            with self.assertRaises(StorageError):
                self.store.mutate(self.initial["revision"], lambda p: p.update(example="new"))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob(".progress.json.*.tmp")), [])

    def test_file_fsync_failure_keeps_previous_json(self):
        before = self.store.path.read_bytes()
        with patch("app.server.progress.os.fsync", side_effect=OSError("模拟磁盘写入失败")):
            with self.assertRaises(StorageError):
                self.store.mutate(self.initial["revision"], lambda p: p.update(example="new"))
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_flush_and_short_write_failures_keep_previous_json(self):
        before = self.store.path.read_bytes()
        real_fdopen = os.fdopen
        for failure in ("flush", "short_write"):
            with self.subTest(failure=failure):
                class BrokenFile:
                    def __init__(self, descriptor, mode):
                        self.file = real_fdopen(descriptor, mode)
                    def __enter__(self):
                        return self
                    def __exit__(self, *args):
                        self.file.close()
                    def write(self, raw):
                        if failure == "short_write":
                            return self.file.write(raw[:7])
                        return self.file.write(raw)
                    def flush(self):
                        raise OSError("模拟缓冲刷新失败")
                with patch("app.server.progress.os.fdopen", side_effect=BrokenFile):
                    with self.assertRaises(StorageError):
                        self.store.mutate(self.initial["revision"], lambda p: p.update(example="new"))
                self.assertEqual(self.store.path.read_bytes(), before)
                self.assertEqual(list(self.directory.glob(".progress.json.*.tmp")), [])

    def test_partial_write_failure_does_not_reach_official_file(self):
        before = self.store.path.read_bytes()
        real_fdopen = os.fdopen
        class PartialFile:
            def __init__(self, descriptor, mode):
                self.file = real_fdopen(descriptor, mode)
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.file.close()
            def write(self, raw):
                self.file.write(raw[:7])
                raise OSError("模拟磁盘空间不足")
        with patch("app.server.progress.os.fdopen", side_effect=PartialFile):
            with self.assertRaises(StorageError):
                self.store.mutate(self.initial["revision"], lambda p: p.update(example="new"))
        self.assertEqual(self.store.path.read_bytes(), before)
        self.assertEqual(list(self.directory.glob(".progress.json.*.tmp")), [])

    def test_directory_fsync_failure_reports_error_but_json_is_complete(self):
        with patch("app.server.progress._fsync_directory", side_effect=OSError("模拟目录同步失败")):
            with self.assertRaises(StorageError):
                self.store.mutate(self.initial["revision"], lambda p: p.update(example="complete"))
        self.assertEqual(json.loads(self.store.path.read_bytes())["example"], "complete")
        # An unacknowledged retry cannot overwrite a successfully replaced file.
        with self.assertRaises(ConflictError):
            self.store.mutate(self.initial["revision"], lambda p: p.update(example="retry"))

    def test_two_processes_one_revision_yield_one_success_one_409(self):
        context = multiprocessing.get_context("spawn")
        gate, queue = context.Event(), context.Queue()
        workers = [context.Process(target=_concurrent_writer,
                                   args=(str(self.directory), self.initial["revision"], gate, queue, marker))
                   for marker in ("one", "two")]
        for worker in workers:
            worker.start()
        gate.set()
        results = [queue.get(timeout=20), queue.get(timeout=20)]
        for worker in workers:
            worker.join(timeout=20)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(sorted(result[0] for result in results), ["conflict", "ok"])
        self.assertIn(("conflict", 409), results)
        self.assertIn(self.store.read()["progress"]["writer"], ("one", "two"))

    def test_killed_writer_leaves_whole_old_or_whole_new_json(self):
        context = multiprocessing.get_context("spawn")
        for after_replace in (False, True):
            with self.subTest(after_replace=after_replace):
                current = self.store.read()
                reached = context.Event()
                child = context.Process(target=_crash_writer,
                                        args=(str(self.directory), current["revision"], after_replace, reached))
                child.start()
                try:
                    self.assertTrue(reached.wait(20), "writer did not reach the replacement boundary")
                    child.kill()
                    child.join(timeout=20)
                    expected_signal = signal.SIGTERM if os.name == "nt" else signal.SIGKILL
                    self.assertEqual(child.exitcode, -expected_signal)
                finally:
                    if child.is_alive():
                        child.kill()
                        child.join(timeout=5)
                value = json.loads(self.store.path.read_bytes())
                if after_replace:
                    self.assertEqual(value["crash_marker"], "whole new value")
                else:
                    self.assertEqual(value, current["progress"])
                self.assertFalse(ProgressStore(self.directory).startup()["read_only"])

    def test_21_starts_keep_only_latest_20_raw_backups(self):
        for value in range(21):
            current = self.store.read()
            self.store.mutate(current["revision"], lambda p, value=value: p.update(sequence=value))
            self.store.startup()
        files = list(self.store.backups.glob("progress-*.json"))
        self.assertEqual(len(files), 20)
        sequences = sorted(json.loads(path.read_bytes())["sequence"] for path in files)
        self.assertEqual(sequences, list(range(1, 21)))

    def test_backup_failure_never_migrates_or_prunes_old_copies(self):
        raw = b'{"spec_version":1,"cards":{},"events":[]}\n'
        self.write_raw(raw)
        backups_before = {path.name: path.read_bytes() for path in self.store.backups.glob("*.json")}
        with patch("app.server.progress._atomic_write", side_effect=OSError("模拟备份失败")):
            stopped = ProgressStore(self.directory).startup()
        self.assertTrue(stopped["read_only"])
        self.assertEqual(self.store.path.read_bytes(), raw)
        self.assertEqual(backups_before, {path.name: path.read_bytes() for path in self.store.backups.glob("*.json")})

    def test_corrupt_main_and_latest_backup_restore_older_valid_copy(self):
        current, _ = self.store.mutate(self.initial["revision"], lambda p: p.update(important="旧备份可用"))
        self.store.startup()
        newest_bad = self.store.backups / "progress-99999999-corrupt.json"
        newest_bad.write_bytes(b"{broken backup")
        bad = b'{"spec_version":2,"cards":'
        self.write_raw(bad)
        recovered = ProgressStore(self.directory).startup()
        self.assertFalse(recovered["read_only"])
        self.assertEqual(recovered["progress"], current["progress"])
        self.assertEqual([path.read_bytes() for path in self.directory.glob("progress.corrupt-*.json")], [bad])
        self.assertTrue(any("恢复" in notice for notice in recovered["notices"]))

    def test_recovery_from_old_format_backs_up_and_migrates_once(self):
        for path in self.store.backups.glob("*.json"):
            path.unlink()
        v1 = b'{"spec_version":1,"cards":{},"events":[],"unknown":"original"}\n'
        (self.store.backups / "progress-20261003-000000.json").write_bytes(v1)
        self.write_raw(b"bad current")
        recovered = ProgressStore(self.directory).startup()
        self.assertFalse(recovered["read_only"])
        self.assertEqual(recovered["progress"]["spec_version"], 2)
        self.assertEqual(recovered["progress"]["unknown"], "original")
        self.assertGreaterEqual(sum(path.read_bytes() == v1 for path in self.store.backups.glob("*.json")), 2)
        self.assertEqual(ProgressStore(self.directory).startup()["progress"], recovered["progress"])

    def test_structurally_invalid_fields_preserve_original_and_restore_backup(self):
        invalid_fields = ({"summary": None}, {"checks": {"c1": {}}},
                          {"checks": {"c1": {"attempts": [{"correct": False}]}}})
        for index, changes in enumerate(invalid_fields):
            with self.subTest(changes=changes):
                directory = Path(self.temp.name) / ("malformed-" + str(index))
                store = ProgressStore(directory)
                store.startup()
                malformed = new_progress()
                malformed["cards"]["retired-or-active-card"] = {
                    "status": "in_progress", "ui": {"learn_completed": False}, **changes}
                original = json.dumps(malformed).encode()
                store.path.write_bytes(original)
                recovered = ProgressStore(directory).startup()
                self.assertFalse(recovered["read_only"])
                self.assertEqual(recovered["progress"], new_progress())
                self.assertEqual([path.read_bytes() for path in directory.glob("progress.corrupt-*.json")], [original])

    def test_null_summary_recovers_before_real_http_today_and_journal(self):
        import threading
        from urllib.request import urlopen
        from app.tests.helpers import temporary_project
        from app.server.main import LearningHub, HubHTTPServer
        with temporary_project() as root:
            hub = LearningHub(root)
            initial = hub.api("GET", "/api/bootstrap")
            cid = initial["cards"][0]["id"]
            hub.api("POST", "/api/op", {"op": "save_summary", "card": cid,
                                        "text": "备份中的有效总结", "revision": initial["revision"]})
            hub.store.startup()  # Retain this valid record as an ordinary startup backup.
            malformed = json.loads(hub.store.path.read_bytes())
            malformed["cards"][cid]["summary"] = None
            original = json.dumps(malformed, ensure_ascii=False).encode()
            hub.store.path.write_bytes(original)
            restored = LearningHub(root)
            server = HubHTTPServer(("127.0.0.1", 0), restored)
            worker = threading.Thread(target=server.serve_forever,
                                      kwargs={"poll_interval": .01}, daemon=True)
            worker.start()
            try:
                for endpoint in ("/api/bootstrap", "/api/today", "/api/journal"):
                    with urlopen("http://127.0.0.1:%s%s" % (server.server_address[1], endpoint), timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        payload = json.load(response)
                        if endpoint == "/api/bootstrap":
                            self.assertFalse(payload["read_only"])
                            self.assertEqual(payload["progress"]["cards"][cid]["summary"], "备份中的有效总结")
                            self.assertTrue(any("恢复" in text for text in payload["notices"]))
                self.assertEqual([path.read_bytes() for path in restored.store.directory.glob("progress.corrupt-*.json")], [original])
            finally:
                server.shutdown()
                server.server_close()
                worker.join()

    def test_all_backups_bad_stays_read_only_even_after_restart(self):
        for path in self.store.backups.glob("*.json"):
            path.write_bytes(b"bad")
        self.write_raw(b"corrupt original")
        for _ in range(2):
            stopped_store = ProgressStore(self.directory)
            stopped = stopped_store.startup()
            self.assertTrue(stopped["read_only"])
            self.assertFalse(stopped_store.path.exists())
            with self.assertRaises(StorageError):
                stopped_store.mutate(stopped["revision"], lambda p: p.update(destroyed=True))
        self.assertEqual([path.read_bytes() for path in self.directory.glob("progress.corrupt-*.json")], [b"corrupt original"])

    def test_higher_version_is_neither_downgraded_nor_quarantined(self):
        raw = b'{"spec_version":99,"cards":{},"events":[],"unknown":[5]}\n'
        self.write_raw(raw)
        stopped_store = ProgressStore(self.directory)
        stopped = stopped_store.startup()
        self.assertTrue(stopped["read_only"])
        self.assertEqual(stopped["progress"]["spec_version"], 99)
        with self.assertRaises(StorageError):
            stopped_store.mutate(stopped["revision"], lambda p: p.update(spec_version=2))
        self.assertEqual(stopped_store.path.read_bytes(), raw)
        self.assertEqual(list(self.directory.glob("progress.corrupt-*.json")), [])

    def test_runtime_external_damage_pauses_writes_without_replacing_it(self):
        self.write_raw(b"someone wrote broken bytes")
        stopped = self.store.read()
        self.assertTrue(stopped["read_only"])
        with self.assertRaises(StorageError):
            self.store.mutate(stopped["revision"], lambda p: p.clear())
        self.assertEqual(self.store.path.read_bytes(), b"someone wrote broken bytes")

    def test_callback_failure_is_not_persisted(self):
        before = self.store.path.read_bytes()
        def callback(value):
            value["cards"].clear()
            value["events"].append({"not_saved": True})
            raise ValueError("业务条件不满足")
        with self.assertRaises(ValueError):
            self.store.mutate(self.initial["revision"], callback)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_local_timestamp_includes_timezone(self):
        from datetime import datetime
        timestamp = datetime.fromisoformat(storage.local_now())
        self.assertIsNotNone(timestamp.tzinfo)

    def test_duplicate_json_fields_are_corruption_not_silent_overwrite(self):
        raw = b'{"spec_version":2,"cards":{},"cards":{},"events":[],"pending_new_cards":[]}'
        self.write_raw(raw)
        recovered = ProgressStore(self.directory).startup()
        self.assertFalse(recovered["read_only"])
        self.assertEqual([p.read_bytes() for p in self.directory.glob("progress.corrupt-*.json")], [raw])

    def test_settings_are_atomic_independent_and_validated(self):
        progress_before = self.store.path.read_bytes()
        value = self.store.put_settings({"theme": "paper", "mode": "dark", "motion": "reduce"})
        self.assertEqual(value["reading"], "sans")
        self.assertEqual(ProgressStore(self.directory).get_settings(), value)
        self.assertEqual(self.store.path.read_bytes(), progress_before)
        before = self.store.settings_path.read_bytes()
        with patch("app.server.progress.os.replace", side_effect=OSError("模拟设置替换失败")):
            with self.assertRaises(StorageError):
                self.store.put_settings({"theme": "clear"})
        self.assertEqual(self.store.settings_path.read_bytes(), before)
        for invalid in ({"theme": "unknown"}, {"unknown": True}, {"sidebar_collapsed": "yes"}):
            with self.assertRaises(ValueError):
                self.store.put_settings(invalid)
        self.assertEqual(self.store.settings_path.read_bytes(), before)

    def test_card_tips_flag_lives_in_settings_and_old_files_default_to_unseen(self):
        progress_before = self.store.path.read_bytes()
        older = {key: value for key, value in storage.DEFAULT_SETTINGS.items() if key != "card_tips_seen"}
        self.store.settings_path.write_bytes(json.dumps(older).encode("utf-8"))
        self.assertIs(self.store.get_settings()["card_tips_seen"], False)
        self.assertIs(self.store.put_settings({"card_tips_seen": True})["card_tips_seen"], True)
        self.assertIs(ProgressStore(self.directory).get_settings()["card_tips_seen"], True)
        self.assertEqual(self.store.path.read_bytes(), progress_before)
        with self.assertRaises(ValueError):
            self.store.put_settings({"card_tips_seen": "yes"})

    def test_corrupt_settings_fallback_does_not_overwrite_original(self):
        self.store.settings_path.write_bytes(b"broken settings")
        self.assertEqual(self.store.get_settings(), storage.DEFAULT_SETTINGS)
        with self.assertRaises(ValueError):
            self.store.put_settings({"theme": "paper"})
        self.assertEqual(self.store.settings_path.read_bytes(), b"broken settings")

    def test_symlink_storage_leaf_or_record_is_refused(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        linked = Path(self.temp.name) / "linked"
        symlink_or_skip(self, linked, outside, directory=True)
        with self.assertRaises(StorageError):
            ProgressStore(linked).startup()
        original = self.store.path.read_bytes()
        self.store.path.unlink()
        target = outside / "untouched.json"
        target.write_bytes(original)
        symlink_or_skip(self, self.store.path, target)
        self.assertTrue(ProgressStore(self.directory).startup()["read_only"])
        self.assertEqual(target.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
