import threading
import time
import unittest

from app.server.lifecycle import Lifecycle, LifecycleError


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        self.life = Lifecycle(clock=lambda: self.now, timeout=60)

    def test_timeout_boundary_and_late_heartbeat(self):
        self.now = 59.999
        self.assertFalse(self.life.should_stop())
        self.life.heartbeat()
        self.now = 119.998
        self.assertFalse(self.life.should_stop())
        self.now = 120
        self.assertTrue(self.life.should_stop())
        with self.assertRaises(LifecycleError):
            self.life.heartbeat()

    def test_default_fallback_survives_throttled_hidden_windows(self):
        life = Lifecycle(clock=lambda: self.now)
        self.now = 25 * 60
        self.assertFalse(life.should_stop())
        self.now = 30 * 60
        self.assertTrue(life.should_stop())
        self.assertEqual(life.stop_reason, 'no heartbeat for 1800 seconds')

    def test_window_owned_service_never_expires_and_records_reason(self):
        life = Lifecycle(clock=lambda: self.now, timeout=None)
        self.now = 24 * 3600
        self.assertFalse(life.should_stop())
        life.request_stop(defer_transaction=True, reason='window process 42 exited')
        self.assertTrue(life.should_stop())
        self.assertEqual(life.stop_reason, 'window process 42 exited')

    def test_shutdown_request_reason_is_kept(self):
        self.life.request_stop()
        self.life.request_stop(defer_transaction=True, reason='signal 15')
        self.assertTrue(self.life.should_stop())
        self.assertEqual(self.life.stop_reason, 'shutdown requested by the window')

    def test_process_alive_parent_and_missing_pid(self):
        import os
        from app.platform_support import process_alive
        self.assertTrue(process_alive(os.getpid()))
        if os.name != 'nt':
            self.assertTrue(process_alive(os.getppid(), parent=True))
            self.assertFalse(process_alive(os.getppid() + 1 if os.getppid() + 1 != os.getpid() else os.getppid() + 2, parent=True))
        self.assertFalse(process_alive(2 ** 22 + 12345))

    def test_active_save_delays_stop_and_closes_admission(self):
        with self.life.operation():
            self.now = 100
            self.life.request_stop()
            self.assertFalse(self.life.should_stop())
            with self.assertRaises(LifecycleError):
                with self.life.operation():
                    self.fail('new writer admitted during shutdown')
        self.assertTrue(self.life.should_stop())

    def test_update_excludes_new_work_but_never_blocks_heartbeat(self):
        self.life.reserve_update()
        self.now = 120
        self.assertFalse(self.life.should_stop())
        self.assertTrue(self.life.heartbeat()['updating'])
        with self.assertRaises(LifecycleError):
            self.life.request_stop()
        with self.assertRaises(LifecycleError):
            with self.life.operation():
                pass
        self.life.finish_update()
        self.assertFalse(self.life.should_stop())
        self.now += 60
        self.assertTrue(self.life.should_stop())

    def test_signal_waits_for_transaction(self):
        self.life.reserve_update()
        self.life.request_stop(defer_transaction=True)
        self.assertFalse(self.life.should_stop())
        self.life.finish_update()
        self.assertTrue(self.life.should_stop())

    def test_update_waits_for_accepted_writer_and_rejects_second_job(self):
        drained = threading.Event()
        with self.life.operation():
            self.life.reserve_update()
            with self.assertRaises(LifecycleError):
                self.life.reserve_update()
            waiter = threading.Thread(target=lambda: (self.life.wait_drained(), drained.set()))
            waiter.start()
            self.assertFalse(drained.wait(.03))
        self.assertTrue(drained.wait(1))
        waiter.join()
        self.life.finish_update()

    def test_failed_request_always_releases_active_count(self):
        with self.assertRaises(ValueError):
            with self.life.operation():
                raise ValueError('failure')
        self.assertEqual(self.life.snapshot()['active'], 0)


class ServiceProcessTests(unittest.TestCase):
    def test_duplicate_start_and_authenticated_shutdown_leave_no_process(self):
        import json
        import subprocess
        import sys
        import tempfile
        from pathlib import Path
        from urllib.request import build_opener, ProxyHandler, Request
        from app.tests.test_updater import fixture_files, materialize
        from app.tests.helpers import REPO
        with tempfile.TemporaryDirectory(prefix='learning-hub-cli-') as directory:
            root = Path(directory).resolve()
            materialize(root, fixture_files())
            runtime = root / 'logs/runtime'
            ready = runtime / 'first.json'
            command = [sys.executable, '-B', '-m', 'app.server.main', '--root', str(root),
                       '--port', '0', '--desktop', '--ready-file', str(ready), '--token', 'temporary-test-token']
            child = subprocess.Popen(command, cwd=str(REPO), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(ready.exists())
                value = json.loads(ready.read_text(encoding='utf-8'))
                opener = build_opener(ProxyHandler({}))
                with opener.open(value['url'] + '/api/health', timeout=2) as response:
                    health = json.load(response)
                self.assertEqual(health['pid'], child.pid)
                before = list((root / 'progress/backups').glob('*')) if (root / 'progress/backups').exists() else []
                second_ready = runtime / 'second.json'
                second_command = command.copy()
                second_command[second_command.index('--ready-file') + 1] = str(second_ready)
                second = subprocess.run(second_command, cwd=str(REPO), capture_output=True, text=True, timeout=10)
                self.assertEqual(second.returncode, 0, second.stderr)
                attached = json.loads(second_ready.read_text(encoding='utf-8'))
                self.assertTrue(attached['existing'])
                self.assertEqual(attached['pid'], child.pid)
                after = list((root / 'progress/backups').glob('*')) if (root / 'progress/backups').exists() else []
                self.assertEqual(before, after)
                request = Request(value['url'] + '/api/shutdown', data=b'{}', headers={
                    'Content-Type': 'application/json', 'X-Learning-Control': value['token']})
                with opener.open(request, timeout=2) as response:
                    self.assertTrue(json.load(response)['stopping'])
                stdout, stderr = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, stderr)
                self.assertNotIn(value['token'], stdout)
                self.assertFalse((runtime / 'server.json').exists())
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.communicate(timeout=5)

    def test_log_retention_only_deletes_own_logs(self):
        import tempfile
        from pathlib import Path
        from app.server.main import configure_log, runtime_directory
        with tempfile.TemporaryDirectory(prefix='learning-hub-log-') as directory:
            root = Path(directory).resolve()
            runtime_directory(root)
            unrelated = root / 'logs/notes.txt'
            unrelated.write_text('keep', encoding='utf-8')
            for unused in range(12):
                logger, handler = configure_log(root)
                logger.info('test lifecycle')
                handler.close()
                logger.removeHandler(handler)
            self.assertEqual(len(list((root / 'logs').glob('learning-hub-*.log'))), 10)
            self.assertEqual(unrelated.read_text(encoding='utf-8'), 'keep')


if __name__ == '__main__':
    unittest.main()
