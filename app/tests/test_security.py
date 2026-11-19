from app.tests.helpers import symlink_or_skip
"""HTTP security checks against an isolated server and fake learning records."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.server.main import start_server, HubHTTPServer
from app.tests.test_content import (isolated_course, done_record, EXPECTED_SECRET,
                                    SOLUTION_SECRET, yaml)


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='learning-hub-security-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        isolated_course(self.root)
        static = self.root / 'app/static'
        static.mkdir(parents=True)
        (static / 'index.html').write_text('<!doctype html><script>window.settings={{INITIAL_SETTINGS}};</script><h1>学习台</h1>', encoding='utf-8')
        (static / 'app.js').write_text('console.log("local");', encoding='utf-8')
        (self.root / 'workspace').mkdir()
        (self.root / 'workspace/private.txt').write_text('PRIVATE_WORKSPACE_SENTINEL', encoding='utf-8')
        self.server = start_server(self.root, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, method, path, body=None, headers=None):
        headers = dict(headers or {})
        if body is not None:
            body = json.dumps(body).encode('utf-8')
            headers.setdefault('Content-Type', 'application/json')
        client = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=5)
        try:
            client.request(method, path, body, headers)
            response = client.getresponse()
            data = response.read().decode('utf-8')
            return response.status, data, dict(response.getheaders())
        finally:
            client.close()

    def op(self, name, **kwargs):
        snapshot = self.server.hub.store.read()
        return self.request('POST', '/api/op', dict(op=name, revision=snapshot['revision'], **kwargs))

    def unlock_task(self):
        snapshot = self.server.hub.store.read()
        self.server.hub.store.mutate(snapshot['revision'], lambda progress: progress['cards'].update({'lesson-one': done_record()}))

    def assert_no_answer_keys(self, value):
        if isinstance(value, dict):
            self.assertNotIn('expected', value)
            for child in value.values():
                self.assert_no_answer_keys(child)
        elif isinstance(value, list):
            for child in value:
                self.assert_no_answer_keys(child)

    def test_all_read_payloads_exclude_expected_and_closed_solutions(self):
        paths = ['/api/bootstrap', '/api/today', '/api/route', '/api/card/lesson-one', '/api/card/task-one',
                 '/api/lab', '/api/journal?view=timeline', '/api/journal?view=notes', '/api/search-index', '/api/settings']
        for path in paths:
            with self.subTest(path=path):
                status, body, unused = self.request('GET', path)
                self.assertEqual(status, 200, body)
                self.assertNotIn(EXPECTED_SECRET, body)
                self.assertNotIn(SOLUTION_SECRET, body)
                self.assert_no_answer_keys(json.loads(body))

    def test_wrong_checks_errors_and_copy_payloads_do_not_leak(self):
        self.unlock_task()
        for unused in range(3):
            status, body, headers = self.op('check', card='task-one', c='c1', value='wrong-user-input')
            self.assertEqual(status, 200, body)
            self.assertNotIn(EXPECTED_SECRET, body)
            self.assertNotIn(SOLUTION_SECRET, body)
            self.assert_no_answer_keys(json.loads(body))
        for kind in ('help', 'writeup', 'record'):
            status, body, headers = self.request('POST', '/api/export', {'kind': kind, 'card': 'task-one', 'problem': '不明白'})
            self.assertEqual(status, 200, body)
            self.assertNotIn(EXPECTED_SECRET, body)
            self.assertNotIn(SOLUTION_SECRET, body)
        status, body, unused = self.op('complete', card='task-one')
        self.assertEqual(status, 422)
        self.assertNotIn(EXPECTED_SECRET, body)
        self.assertNotIn(SOLUTION_SECRET, body)

    def test_solution_unlock_is_server_enforced_and_explicit(self):
        self.unlock_task()
        status, body, unused = self.op('view_solution', card='task-one')
        self.assertEqual(status, 403)
        self.assertNotIn(SOLUTION_SECRET, body)
        for unused in range(3):
            self.op('check', card='task-one', c='c1', value='wrong')
        self.assertNotIn(SOLUTION_SECRET, self.request('GET', '/api/card/task-one')[1])
        status, body, unused = self.op('view_solution', card='task-one')
        self.assertEqual(status, 200, body)
        self.assertIn(SOLUTION_SECRET, self.request('GET', '/api/card/task-one')[1])
        self.assertNotIn(EXPECTED_SECRET, body)

    def test_locked_card_only_accepts_seen_operation(self):
        status, body, unused = self.op('seen_card', card='task-one')
        self.assertEqual(status, 200, body)
        for operation in ('start_card', 'save_summary', 'view_solution', 'complete'):
            status, body, unused = self.op(operation, card='task-one', text='test')
            self.assertEqual(status, 403, (operation, body))
        self.assertNotIn('task-one', self.server.hub.store.read()['progress']['cards'])

    def test_static_allowlist_and_encoded_traversal(self):
        for path in ('/content/course.yaml', '/content/cards/alpha/task-one.md', '/data/generators/task-one.answers.json',
                     '/progress/progress.json', '/workspace/private.txt', '/app/server/main.py', '/preview/index.html',
                     '/static/../../content/course.yaml', '/static/%2e%2e/%2e%2e/workspace/private.txt',
                     '/vendor/%2e%2e/%2e%2e/progress/progress.json', '/static/..%5c..%5cworkspace/private.txt',
                     '/static/%00', '/static//app.js', '/static/./app.js'):
            with self.subTest(path=path):
                status, body, unused = self.request('GET', path)
                self.assertIn(status, (400, 403, 404), body)
                self.assertNotIn(EXPECTED_SECRET, body)
                self.assertNotIn('PRIVATE_WORKSPACE_SENTINEL', body)
        self.assertEqual(self.request('GET', '/static/app.js')[0], 200)

    def test_static_symlinks_and_index_symlink_are_denied(self):
        target = self.root / 'workspace/private.txt'
        symlink_or_skip(self, self.root / 'app/static/link.txt', target)
        status, body, unused = self.request('GET', '/static/link.txt')
        self.assertEqual(status, 403)
        self.assertNotIn('PRIVATE_WORKSPACE_SENTINEL', body)
        index = self.root / 'app/static/index.html'
        index.unlink()
        symlink_or_skip(self, index, target)
        status, body, unused = self.request('GET', '/')
        self.assertEqual(status, 403)
        self.assertNotIn('PRIVATE_WORKSPACE_SENTINEL', body)

    def test_windows_static_junction_is_rejected_before_read(self):
        original = Path.lstat
        junction = self.root / 'app/static'
        def lstat(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == junction:
                return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
            return info
        with patch.object(Path, 'lstat', lstat):
            self.assertEqual(self.request('GET', '/static/app.js')[0], 403)
            self.assertEqual(self.request('GET', '/')[0], 403)

    def test_cross_site_origin_and_rebinding_host_are_rejected(self):
        for headers in ({'Origin': 'https://evil.example'}, {'Host': 'evil.example'},
                        {'Sec-Fetch-Site': 'cross-site'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.request('GET', '/api/bootstrap', headers=headers)[0], 403)
                self.assertEqual(self.request('POST', '/api/op', {'op': 'visit'}, headers=headers)[0], 403)

    def test_safe_headers_and_default_theme_in_initial_html(self):
        status, html, headers = self.request('GET', '/')
        self.assertEqual(status, 200)
        self.assertIn('"theme": "clear"', html)
        self.assertNotIn('{{INITIAL_SETTINGS}}', html)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_reveal_allows_only_listed_data_for_unlocked_card(self):
        with patch('app.server.main.reveal_file') as reveal:
            self.assertEqual(self.request('POST', '/api/reveal', {'card': 'task-one', 'file': 'sample.txt'})[0], 403)
            self.unlock_task()
            for path in ('../sample.txt', '../../../workspace/private.txt', '/etc/passwd', 'unlisted.txt', 'sample.txt; open anything'):
                self.assertEqual(self.request('POST', '/api/reveal', {'card': 'task-one', 'file': path})[0], 403)
            reveal.assert_not_called()
            status, body, unused = self.request('POST', '/api/reveal', {'card': 'task-one', 'file': 'sample.txt'})
            self.assertEqual(status, 200, body)
            reveal.assert_called_once_with(self.root / 'data/tasks/task-one/sample.txt')

    def test_invalid_content_is_reported_without_breaking_other_cards(self):
        (self.root / 'content/cards/alpha/task-one.md').write_text('---\nid: [broken\n---\n' + EXPECTED_SECRET, encoding='utf-8')
        status, body, unused = self.request('GET', '/api/bootstrap')
        self.assertEqual(status, 200, body)
        decoded = json.loads(body)
        self.assertTrue(decoded['issues'])
        self.assertIn('lesson-one', [card['id'] for card in decoded['cards']])
        self.assertNotIn(EXPECTED_SECRET, body)
        self.assertEqual(self.request('GET', '/api/card/task-one')[0], 404)

    def test_interrupted_update_returns_503_instead_of_mixed_content(self):
        (self.root / 'backups').mkdir(exist_ok=True)
        (self.root / 'backups/.content-update-transaction.json').write_text('{}', encoding='utf-8')
        status, body, unused = self.request('GET', '/api/bootstrap')
        self.assertEqual(status, 503)
        self.assertIn('上次内容更新未完成', body)

    def test_non_loopback_bind_is_rejected(self):
        with self.assertRaises(ValueError):
            HubHTTPServer(('0.0.0.0', 0), self.server.hub)

    def test_bad_card_preserves_existing_and_new_pending_ids_across_visit(self):
        snapshot = self.server.hub.store.read()
        self.server.hub.store.mutate(snapshot['revision'], lambda progress: progress.update(
            known_cards=['lesson-one', 'task-one'], pending_new_cards=['task-one'], seen_content_version='2026-10-03.3'))
        for cid in ('task-one', 'lesson-two'):
            (self.root / 'content/cards/alpha' / (cid + '.md')).write_text('---\nid: [broken\n---\n', encoding='utf-8')
        status, body, unused = self.op('visit')
        self.assertEqual(status, 200, body)
        progress = self.server.hub.store.read()['progress']
        self.assertEqual(progress['pending_new_cards'], ['task-one', 'lesson-two'])
        self.assertEqual(progress['known_cards'], ['lesson-one', 'task-one', 'lesson-two'])

    def test_broken_or_unknown_course_cannot_erase_visit_metadata(self):
        snapshot = self.server.hub.store.read()
        self.server.hub.store.mutate(snapshot['revision'], lambda progress: progress.update(
            known_cards=['lesson-one', 'task-one'], pending_new_cards=['task-one'], seen_content_version='2026-10-03.3'))
        course_path = self.root / 'content/course.yaml'
        good = yaml.safe_load(course_path.read_bytes())
        future = dict(good, spec_version=99)
        for bad in ('tracks: [broken', yaml.safe_dump(future)):
            with self.subTest(content=bad[:40]):
                course_path.write_text(bad, encoding='utf-8')
                before = (self.root / 'progress/progress.json').read_bytes()
                status, body, unused = self.request('GET', '/api/bootstrap')
                self.assertEqual(status, 200, body)
                self.assertTrue(json.loads(body)['read_only'])
                status, body, unused = self.op('visit')
                self.assertEqual(status, 503, body)
                self.assertEqual((self.root / 'progress/progress.json').read_bytes(), before)

    def test_default_ports_try_8765_through_8775_and_explicit_port_once(self):
        from app.server import main as server_module
        from unittest.mock import MagicMock
        marker = MagicMock()
        # Bind outcomes are injected so this test neither takes fixed real ports
        # nor depends on whether other local programs are running.
        with patch.object(server_module, 'LearningHub', return_value=self.server.hub), \
                patch.object(server_module, 'HubHTTPServer', side_effect=[OSError('busy')] * 10 + [marker]) as bind:
            self.assertIs(server_module.start_server(self.root), marker)
            self.assertEqual([call.args[0] for call in bind.call_args_list], [('127.0.0.1', n) for n in range(8765, 8776)])
        with patch.object(server_module, 'LearningHub', return_value=self.server.hub), \
                patch.object(server_module, 'HubHTTPServer', side_effect=OSError('busy')) as bind:
            with self.assertRaises(OSError):
                server_module.start_server(self.root, port=8877)
            self.assertEqual(bind.call_count, 1)
