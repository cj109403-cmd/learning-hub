import json
import shutil
import zipfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch
from app.tests.helpers import temporary_project, REPO
from app.server.main import LearningHub, HubHTTPServer
from app.server.content import load_content
from app.vendor_py import yaml


class APITests(unittest.TestCase):
    def setUp(self):
        self.project = temporary_project(static=True)
        self.root = self.project.__enter__()
        self.hub = LearningHub(self.root)
        self.server = HubHTTPServer(('127.0.0.1', 0), self.hub)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:%s' % self.server.server_address[1]
        self.content = load_content(self.root)
        self.revision = self.request('GET', '/api/bootstrap')[1]['revision']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.project.__exit__(None, None, None)

    def request(self, method, path, body=None, headers=None):
        headers = dict({'Content-Type': 'application/json'}, **(headers or {}))
        request = Request(self.url + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method=method)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        raw = response.read()
        return response.status, json.loads(raw) if 'application/json' in response.headers['Content-Type'] else raw.decode()

    def op(self, op, **params):
        status, data = self.request('POST', '/api/op', dict(op=op, revision=self.revision, **params))
        if status == 200:
            self.revision = data['revision']
        return status, data

    def first_task(self):
        """Finish every card before the course's first task, then return that task's id."""
        for cid in self.content.all_ids:
            if self.content.cards[cid]['type'] == 'task':
                return cid
            self.finish(cid)
        self.skipTest('this course has no task card')

    def finish(self, cid):
        card = self.content.cards[cid]
        self.assertEqual(self.op('start_card', card=cid)[0], 200)
        if card['type'] == 'lesson':
            for question in card['questions']:
                self.assertEqual(self.op('save_answer', card=cid, q=question['id'], text='临时测试回答')[0], 200)
                self.assertEqual(self.op('mark_question', card=cid, q=question['id'], mark='correct')[0], 200)
        else:
            for check in card['checks']:
                expected = check['expected']
                value = ','.join(expected) if check['kind'] == 'set' else str(expected)
                self.assertTrue(self.op('check', card=cid, c=check['id'], value=value)[1]['result']['correct'])
            if card.get('writeup'):
                self.op('save_writeup', card=cid, text='临时任务结论。')
                for index in range(len(card['writeup']['rubric'])):
                    self.op('toggle_rubric', card=cid, index=index)
        self.op('save_summary', card=cid, text='临时总结。')
        status, data = self.op('complete', card=cid)
        self.assertEqual(status, 200, data)
        return data

    def test_complete_entire_course_through_runtime_api(self):
        self.op('visit')
        self.op('welcome_done')
        for cid in self.content.all_ids:
            data = self.finish(cid)
            self.assertEqual(data['result']['card']['status'], 'done')
        saved = json.loads((self.root / 'progress/progress.json').read_text(encoding='utf-8'))
        self.assertEqual(len(saved['cards']), len(self.content.all_ids))
        self.assertEqual(saved['spec_version'], 2)
        self.assertEqual(self.request('GET', '/api/today')[1]['sentence'], '这一阶段全部完成了。')
        self.assertIsNone(data['result']['next'])
        self.assertEqual(self.op('save_summary', card=self.content.all_ids[0], text='完成后补充')[1]['cards'][0]['status'], 'done')

    def test_locked_and_incomplete_writes_rejected_without_revision_change(self):
        original = self.revision
        self.assertEqual(self.op('start_card', card=self.content.all_ids[1])[0], 403)
        self.assertEqual(self.op('complete', card=self.content.all_ids[0])[0], 422)
        self.assertEqual(self.request('GET', '/api/bootstrap')[1]['revision'], original)
        self.assertEqual(self.op('seen_card', card=self.content.all_ids[1])[0], 200)

    def test_program_state_applies_to_the_latest_record(self):
        old = self.revision
        self.op('visit')
        status, data = self.request('POST', '/api/op', {'op': 'welcome_done', 'revision': old})
        self.assertEqual(status, 200, data)
        self.assertIn('welcome_seen_at', self.request('GET', '/api/bootstrap')[1]['progress'])

    def test_text_conflicts_only_when_the_same_field_changed(self):
        cid = self.content.all_ids[0]
        self.assertEqual(self.op('start_card', card=cid)[0], 200)
        stale = self.revision
        self.assertEqual(self.op('set_ui', card=cid, phase='practice', anchor='')[0], 200)
        post = lambda **body: self.request('POST', '/api/op', dict(op='save_summary', card=cid, revision=stale, **body))
        # Another write happened (reading position), but the summary itself did not change.
        status, data = post(text='第一句', base='')
        self.assertEqual(status, 200, data)
        # The same save sent again (a retry) is not a conflict.
        self.assertEqual(post(text='第一句', base='')[0], 200)
        # Without a base, a stale revision is still refused as before.
        self.assertEqual(post(text='没有基线')[0], 409)
        # Another window changed the summary after this edit started: a real conflict.
        status, data = post(text='另一个窗口的旧草稿', base='')
        self.assertEqual(status, 409)
        self.assertEqual(data['revision'], self.request('GET', '/api/bootstrap')[1]['revision'])
        record = self.request('GET', '/api/bootstrap')[1]['progress']['cards'][cid]
        self.assertEqual(record['summary'], '第一句')

    def test_checkboxes_carry_the_wanted_state(self):
        cid = next((c for c in self.content.all_ids if self.content.cards[c].get('resources')), None)
        if cid is None or self.content.all_ids.index(cid) != 0:
            self.skipTest('first card has no resources')
        self.assertEqual(self.op('start_card', card=cid)[0], 200)
        for done, expected in ((True, [0]), (True, [0]), (False, []), (False, [])):
            status, data = self.op('toggle_resource', card=cid, index=0, done=done)
            self.assertEqual(status, 200, data)
            self.assertEqual(data['progress']['cards'][cid]['resources_done'], expected)

    def test_settings_before_paint_and_independent_record_revision(self):
        status, settings = self.request('PUT', '/api/settings', {'theme': 'paper', 'mode': 'dark'})
        self.assertEqual(status, 200)
        self.assertEqual(settings['theme'], 'paper')
        html = self.request('GET', '/')[1]
        self.assertNotIn('{{INITIAL_SETTINGS}}', html)
        self.assertIn('"theme": "paper"', html)
        self.assertEqual(self.request('GET', '/api/bootstrap')[1]['revision'], self.revision)
        self.assertEqual(self.request('PUT', '/api/settings', {'theme': 'invalid'})[0], 400)

    def test_content_reloaded_without_server_restart(self):
        path = self.root / 'content/course.yaml'
        course = yaml.safe_load(path.read_text(encoding='utf-8'))
        course['people'][0]['name'] = '测试导师'
        path.write_text(yaml.safe_dump(course, allow_unicode=True), encoding='utf-8')
        self.assertEqual(self.request('GET', '/api/bootstrap')[1]['course']['people'][0]['name'], '测试导师')

    def test_restart_retains_stage_anchor_and_summary(self):
        cid = self.content.all_ids[0]
        self.op('start_card', card=cid)
        self.op('set_ui', card=cid, phase='practice', anchor='q2', learn_completed=True)
        self.op('save_summary', card=cid, text='重启测试')
        restarted = LearningHub(self.root)
        card = restarted.api('GET', '/api/card/' + cid)['card']
        self.assertEqual(card['record']['ui'], {'phase': 'practice', 'anchor': 'q2', 'learn_completed': True})
        self.assertEqual(card['record']['summary'], '重启测试')

    def test_exports_saved_in_temporary_progress(self):
        self.finish(self.content.all_ids[0])
        status, data = self.request('POST', '/api/export', {'kind': 'record'})
        self.assertEqual(status, 200)
        self.assertTrue(Path(data['path']).is_relative_to(self.root / 'progress/exports'))
        self.assertEqual(Path(data['path']).read_text(encoding='utf-8'), data['text'])
        self.assertEqual(data['text'].count('\n## '), 6)

    def test_host_origin_cross_site_and_static_boundaries(self):
        for headers in ({'Host': 'evil.test'}, {'Origin': 'https://evil.test'}, {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.request('GET', '/api/bootstrap', headers=headers)[0], 403)
        task = next(c for c in self.content.all_ids if self.content.cards[c]['type'] == 'task')
        for path in ('/progress/progress.json', '/content/course.yaml', '/data/generators/%s.answers.json' % task, '/static/../server/main.py', '/static/%2e%2e/server/main.py', '/workspace/secret'):
            self.assertEqual(self.request('GET', path)[0], 404)

    def test_wrong_check_returns_no_expected_and_solution_after_three_wrong(self):
        cid = self.first_task()
        check = self.content.cards[cid]['checks'][0]['id']
        self.op('start_card', card=cid)
        self.assertEqual(self.op('view_solution', card=cid)[0], 403)
        for _ in range(3):
            status, result = self.op('check', card=cid, c=check, value='incorrect')
            self.assertEqual(status, 200)
            self.assertFalse(result['result']['correct'])
            self.assertNotIn('expected', json.dumps(result))
            public = next(c for c in result['cards'] if c['id'] == cid)
            self.assertNotIn('参考解法', public['sections'])
        result = self.op('view_solution', card=cid)[1]
        public = next(c for c in result['cards'] if c['id'] == cid)
        self.assertIn('参考解法', public['sections'])

    def test_reveal_only_listed_data_argv_no_shell(self):
        cid = self.first_task()
        if not self.content.cards[cid].get('data'):
            self.skipTest('the first task has no data files')
        file = self.content.cards[cid]['data'][0]['file']
        with patch('app.server.main.reveal_file') as run:
            self.assertEqual(self.request('POST', '/api/reveal', {'card': cid, 'file': file})[0], 200)
            self.assertTrue(str(run.call_args.args[0]).startswith(str(self.root / 'data/tasks')))
            self.assertEqual(run.call_args.kwargs, {})
            run.reset_mock()
            for file in ('../secret', '/etc/passwd', 'other.txt'):
                self.assertEqual(self.request('POST', '/api/reveal', {'card': cid, 'file': file})[0], 403)
            run.assert_not_called()

    def test_runtime_update_preserves_records_and_labels_new_locked_card(self):
        from app.updater.update_content import apply_update
        from app.file_safety import CONTENT_ROOTS
        import tempfile
        self.op('visit')
        first = self.content.all_ids[0]
        self.finish(first)
        self.request('PUT', '/api/settings', {'theme': 'paper'})
        before = {name: (self.root / 'progress' / name).read_bytes() for name in ('progress.json', 'settings.json')}
        for name in CONTENT_ROOTS:
            if (self.root / name).exists():
                continue
            source, target = REPO / name, self.root / name
            if not source.exists():
                continue  # a project without the course authors' notes or design spec
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target) if source.is_dir() else shutil.copy2(source, target)
        with tempfile.TemporaryDirectory() as package_dir:
            staging = Path(package_dir) / 'package'
            for name in CONTENT_ROOTS:
                source, target = self.root / name, staging / name
                if not source.exists():
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source, target) if source.is_dir() else shutil.copy2(source, target)
            course_path = staging / 'content/course.yaml'
            course = yaml.safe_load(course_path.read_text(encoding='utf-8'))
            course['content_version'] = '2026-10-03.999'
            course['tracks'][0]['cards'].append('added-test-card')
            course_path.write_text(yaml.safe_dump(course, allow_unicode=True), encoding='utf-8')
            source = staging / 'content/cards' / self.content.cards[first]['track_id'] / (first + '.md')
            (source.parent / 'added-test-card.md').write_text(source.read_text(encoding='utf-8').replace('id: ' + first, 'id: added-test-card', 1), encoding='utf-8')
            archive = Path(package_dir) / 'learning-hub-update-20261003-999.zip'
            with zipfile.ZipFile(archive, 'w') as zip_file:
                for file in staging.rglob('*'):
                    if file.is_file():
                        zip_file.write(file, file.relative_to(staging).as_posix())
            result = apply_update(self.root, archive)
            self.assertEqual(result['new_version'], '2026-10-03.999')
        for name, raw in before.items():
            self.assertEqual((self.root / 'progress' / name).read_bytes(), raw)
        status, result = self.op('visit')
        self.assertEqual(status, 200, result)
        self.assertEqual(result['new_cards'], ['added-test-card'])
        self.assertEqual(next(c for c in result['cards'] if c['id'] == 'added-test-card')['status'], 'locked')
        self.assertEqual(self.op('seen_card', card='added-test-card')[1]['new_cards'], [])
        self.assertEqual(result['progress']['cards'][first]['status'], 'done')

    def test_interrupted_update_returns_recoverable_503(self):
        backups = self.root / 'backups'
        backups.mkdir(exist_ok=True)
        (backups / '.content-update-transaction.json').write_text('{}', encoding='utf-8')
        status, result = self.request('GET', '/api/bootstrap')
        self.assertEqual(status, 503)
        self.assertIn('上次内容更新未完成', result['message'])


if __name__ == '__main__':
    unittest.main()
