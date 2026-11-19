"""Desktop control routes and real update workers, always in disposable projects."""
import http.client
import io
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from app.server.main import LearningHub, HubHTTPServer
from app.server.updates import version_key
from app.tests.test_updater import fixture_files, materialize, write_zip


class DesktopAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='learning-hub-desktop-api-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        materialize(self.root, fixture_files())
        self.downloads = Path(self.temp.name) / 'Downloads'
        self.downloads.mkdir()
        self.hub = LearningHub(self.root, downloads=self.downloads)
        self.server = HubHTTPServer(('127.0.0.1', 0), self.hub)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01})
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        worker = self.hub.updates.thread
        if worker:
            worker.join(10)
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def request(self, method, path, body=None, *, token=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            headers = {'Content-Type': 'application/json'}
            if token:
                headers['X-Learning-Control'] = self.hub.token
            connection.request(method, path, json.dumps(body).encode() if body is not None else None, headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def wait_job(self):
        self.hub.updates.thread.join(15)
        self.assertFalse(self.hub.updates.thread.is_alive())
        return self.hub.updates.status()

    def package(self, version='2026-10-03.10', extra=()):
        return write_zip(self.downloads / ('learning-hub-update-' + version.replace('.', '-') + '.zip'), fixture_files(version), extra)

    def test_control_routes_require_session_capability(self):
        for path in ('/api/heartbeat', '/api/shutdown', '/api/updates/check', '/api/drafts'):
            self.assertEqual(self.request('POST', path, {}, token=False)[0], 403)
        status, body = self.request('GET', '/api/health', token=False)
        self.assertEqual(status, 200)
        self.assertEqual(body['instance_id'], self.hub.instance_id)
        self.assertNotIn('token', body)

    def test_health_heartbeat_status_do_not_open_records(self):
        self.hub.lifecycle.reserve_update()
        with patch.object(self.hub.store, 'read', side_effect=AssertionError('control read records')):
            for method, path, payload in [('GET', '/api/health', None), ('POST', '/api/heartbeat', {}), ('GET', '/api/updates/status', None)]:
                self.assertEqual(self.request(method, path, payload)[0], 200)
        self.assertEqual(self.request('POST', '/api/shutdown', {})[0], 409)
        self.assertEqual(self.request('PUT', '/api/settings', {'theme': 'paper'})[0], 503)
        self.hub.lifecycle.finish_update()

    def test_real_worker_preserves_records_and_reports_success(self):
        self.package()
        before = (self.root / 'progress/progress.json').read_bytes()
        self.assertEqual(self.request('POST', '/api/updates/check', {})[0], 200)
        candidate = self.wait_job()['candidate']
        self.assertEqual(candidate['new_version'], '2026-10-03.10')
        self.assertEqual(self.request('POST', '/api/updates/apply', {'candidate_id': candidate['id']})[0], 200)
        result = self.wait_job()
        self.assertEqual(result['state'], 'success', result)
        self.assertEqual((self.root / 'progress/progress.json').read_bytes(), before)
        self.assertFalse(self.hub.lifecycle.updating)

    def test_invalid_path_rejected_and_validator_script_never_blocks(self):
        self.package(extra=[('progress/evil.json', b'bad')])
        self.request('POST', '/api/updates/check', {})
        result = self.wait_job()
        self.assertEqual(result['state'], 'error')
        self.assertIn('progress', str(result['issues']))
        for path in self.downloads.iterdir():
            path.unlink()
        files = fixture_files('2026-10-03.11')
        files['tools/validate_content.py'] += b'\n# a newer version of the course checklist\n'
        write_zip(self.downloads / 'learning-hub-update-unknown.zip', files)
        self.request('POST', '/api/updates/check', {})
        result = self.wait_job()
        self.assertNotEqual(result['state'], 'error', result)
        self.assertEqual(result['candidate']['new_version'], '2026-10-03.11')

    def test_broken_course_is_explained_on_the_page(self):
        files = fixture_files('2026-10-03.12')
        files['content/cards/python/first-card.md'] = files['content/cards/python/first-card.md'].replace(
            b"verified: '2026-10-03'", b"verified: soon")
        write_zip(self.downloads / 'learning-hub-update-broken.zip', files)
        self.request('POST', '/api/updates/check', {})
        result = self.wait_job()
        self.assertEqual(result['state'], 'error')
        self.assertIn('first-card：缺少有效的核实日期', str(result))

    def test_checked_archive_changed_cannot_be_applied(self):
        path = self.package()
        self.request('POST', '/api/updates/check', {})
        candidate = self.wait_job()['candidate']
        before = (self.root / 'content/course.yaml').read_bytes()
        path.write_bytes(path.read_bytes() + b'changed')
        self.request('POST', '/api/updates/apply', {'candidate_id': candidate['id']})
        result = self.wait_job()
        self.assertEqual(result['state'], 'error')
        self.assertIn('改变', result['message'])
        self.assertEqual((self.root / 'content/course.yaml').read_bytes(), before)

    def test_downloads_junction_rejected_before_directory_scan(self):
        original = Path.lstat
        def info(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == self.downloads:
                return SimpleNamespace(st_mode=value.st_mode, st_file_attributes=0x400)
            return value
        with patch.object(Path, 'lstat', info), patch('app.server.updates.os.scandir', side_effect=AssertionError('must not scan link target')):
            self.request('POST', '/api/updates/check', {})
            state = self.wait_job()
        self.assertEqual(state['state'], 'error')
        self.assertIn('不能使用符号链接', state['message'])

    def test_batch_versions_are_compared_numerically(self):
        self.assertGreater(version_key('2026-10-03.10'), version_key('2026-10-03.9'))
        self.package('2026-10-03.10')
        self.package('2026-10-03.9')
        self.request('POST', '/api/updates/check', {})
        self.assertEqual(self.wait_job()['candidate']['new_version'], '2026-10-03.10')

    def test_malformed_worker_output_and_drain_failure_release_admission(self):
        self.package()
        self.request('POST', '/api/updates/check', {})
        candidate = self.wait_job()['candidate']
        child = MagicMock()
        child.stdout = io.BytesIO(b'\xff\n')
        child.poll.return_value = None
        child.communicate.side_effect = OSError('pipe closed')
        child.wait.return_value = 1
        # Keep the real validation process; replace only the update worker.
        import subprocess
        real_popen = subprocess.Popen
        def launch(command, **kwargs):
            if 'app.updater.update_content' in command:
                self.assertIn('--expected-content-version', command)
                self.assertEqual(command[-1], candidate['old_version'])
                return child
            return real_popen(command, **kwargs)
        with patch('app.server.updates.subprocess.Popen', side_effect=launch):
            self.request('POST', '/api/updates/apply', {'candidate_id': candidate['id']})
            result = self.wait_job()
        self.assertEqual(result['state'], 'error')
        self.assertFalse(self.hub.lifecycle.updating)
        self.assertTrue(child.stdout.closed)
        child.wait.assert_called_once()
        self.assertEqual(self.request('PUT', '/api/settings', {'theme': 'paper'})[0], 200)

    def test_explicit_draft_resolution_and_forged_confirmation(self):
        entry = dict(client_id='client123', card='first-card', field='summary', sequence=1, text='永久保留的草稿')
        status, body = self.request('POST', '/api/drafts', entry)
        self.assertEqual(status, 200, body)
        key = {k: body['draft'][k] for k in ('id', 'sequence')}
        self.assertFalse(self.request('POST', '/api/drafts/confirm', key)[1]['confirmed'])
        self.assertEqual(len(self.request('GET', '/api/drafts')[1]['drafts']), 1)
        self.assertTrue(self.request('POST', '/api/drafts/discard', key)[1]['discarded'])
        self.assertEqual(self.request('GET', '/api/drafts')[1]['drafts'], [])
        status, late = self.request('POST', '/api/drafts', entry)
        self.assertEqual(status, 200)
        self.assertTrue(late['draft']['resolved'])
        self.assertEqual(self.request('GET', '/api/drafts')[1]['drafts'], [])

    def test_completed_record_allows_matching_draft_confirmation(self):
        entry = dict(client_id='client123', card='first-card', field='summary', sequence=1, text='正式保存')
        draft = self.request('POST', '/api/drafts', entry)[1]['draft']
        revision = self.hub.store.read()['revision']
        status, body = self.request('POST', '/api/op', {'op': 'save_summary', 'card': 'first-card', 'text': entry['text'], 'revision': revision})
        self.assertEqual(status, 200, body)
        self.assertTrue(self.request('POST', '/api/drafts/confirm', {k: draft[k] for k in ('id', 'sequence')})[1]['confirmed'])


if __name__ == '__main__':
    unittest.main()
