"""Update diagnostics must not bypass the learning API's answer boundary."""
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.server.main import LearningHub, HubHTTPServer
from app.tests.test_content import task_meta
from app.tests.test_updater import fixture_files, materialize, write_zip, yaml, snapshot

EXPECTED = 'PRIVATE_EXPECTED_IN_UPDATE_31c4'
GENERATOR = 'PRIVATE_GENERATOR_IN_UPDATE_042b'
SOLUTION = 'PRIVATE_SOLUTION_IN_UPDATE_741a'


class UpdatePrivacyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='learning-hub-update-privacy-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / 'project'; self.root.mkdir()
        self.downloads = self.base / 'Downloads'; self.downloads.mkdir()
        self.old = fixture_files('2026-10-03.1')
        materialize(self.root, self.old)
        self.hub = LearningHub(self.root, downloads=self.downloads)
        self.server = HubHTTPServer(('127.0.0.1', 0), self.hub)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        worker = self.hub.updates.thread
        if worker:
            worker.join(15)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, method, path, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            headers = {'Content-Type': 'application/json', 'X-Learning-Control': self.hub.token}
            connection.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
            response = connection.getresponse()
            raw = response.read().decode('utf-8')
            for secret in (EXPECTED, GENERATOR, SOLUTION):
                self.assertNotIn(secret, raw)
            return response.status, json.loads(raw)
        finally:
            connection.close()

    def invalid_task_package(self, *, choice=False):
        files = fixture_files('2026-10-03.2')
        task = task_meta('first-card')
        task['checks'][0]['expected'] = EXPECTED
        if choice:
            task['checks'][0].update(kind='choice', options=[{'id': 'a', 'text': '选项一'}, {'id': 'b', 'text': '选项二'}])
        files['content/cards/python/first-card.md'] = ('---\n' + yaml.safe_dump(task, allow_unicode=True) +
            '---\n## 任务说明\n测试任务\n## 提示\n### 提示 1\n测试提示\n## 参考解法\n' + SOLUTION + '\n').encode()
        files['data/tasks/first-card/sample.txt'] = b'test data'
        files['data/generators/first-card.answers.json'] = json.dumps({'checks': {'c1': GENERATOR}}).encode()
        return write_zip(self.downloads / 'learning-hub-update-test.zip', files)

    def checked_error(self):
        code, unused = self.request('POST', '/api/updates/check', {})
        self.assertEqual(code, 200)
        self.hub.updates.thread.join(10)
        self.assertFalse(self.hub.updates.thread.is_alive())
        code, state = self.request('GET', '/api/updates/status')
        self.assertEqual(code, 200)
        self.assertEqual(state['state'], 'error')
        self.assertEqual(snapshot(self.root), self.old)
        return state

    def test_validator_answer_mismatch_does_not_leak_through_check_status(self):
        self.invalid_task_package()
        state = self.checked_error()
        self.assertIn('内容检查', str(state))

    def test_choice_expected_error_does_not_leak_through_check_status(self):
        self.invalid_task_package(choice=True)
        state = self.checked_error()
        self.assertIn('内容检查', str(state))

    def test_worker_validation_error_is_redacted_after_real_rollback(self):
        path = self.invalid_task_package()
        fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
        report = {'fingerprint': fingerprint, 'old_version': '2026-10-03.1',
                  'new_version': '2026-10-03.2', 'new_cards': []}
        public = dict(report, id=fingerprint, filename=path.name)
        # Force the candidate recheck to pass so the real worker's validator
        # rejects after replacement. This directly exercises worker-error UI,
        # not just the earlier preview rejection path.
        self.hub.lifecycle.reserve_update()
        with patch('app.server.updates.inspect_update', return_value=report):
            self.hub.updates._apply(public, path)
        code, state = self.request('GET', '/api/updates/status')
        self.assertEqual(code, 200)
        self.assertEqual(state['state'], 'error')
        self.assertIn('恢复原样', state['message'])
        self.assertEqual(snapshot(self.root), self.old)
        self.assertFalse(self.hub.lifecycle.updating)

    def test_yaml_diagnostic_cannot_echo_content_source_to_update_api(self):
        files = fixture_files('2026-10-03.2')
        files['content/cards/python/first-card.md'] = ('---\nid: first-card\nchecks: [' + EXPECTED + '\n---\n## 讲解\n' + SOLUTION).encode()
        write_zip(self.downloads / 'learning-hub-update-broken-yaml.zip', files)
        self.checked_error()
