from app.tests.helpers import symlink_or_skip
"""Content fixtures are generated under temporary roots, never edited in place."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.server.content import load_content, split_markdown, subheadings, split_hints, content_path
from app.vendor_py import yaml

EXPECTED_SECRET = 'EXPECTED_NEVER_SEND_f4d7'
SOLUTION_SECRET = 'LOCKED_SOLUTION_NEVER_SEND_9a26'
ERROR_FIXTURES = Path(__file__).with_name('fixtures') / 'content'


def lesson_meta(cid='lesson-one'):
    return dict(id=cid, type='lesson', title='测试学习卡 ' + cid, subtitle='用于隔离测试', minutes=30,
                requires=[], verified='2026-10-03', resources=[], questions=[
                    dict(id='q1', kind='predict', prompt='预测结果？', answer='参考解释一'),
                    dict(id='q2', kind='concept', prompt='什么是变量？', answer='参考解释二'),
                    dict(id='q3', kind='apply', prompt='如何用于实验？', answer='参考解释三')])


def task_meta(cid='task-one'):
    return dict(id=cid, type='task', title='测试任务 ' + cid, subtitle='用于隔离测试', minutes=35,
                requires=[], verified='2026-10-03', resources=[], **{
                    'from': 'mentor', 'message': '第一段导师消息。\n\n第二段导师消息。',
                    'simulated': True, 'capstone': False, 'debrief': '这是一条完成后的回复。',
                    'data': [{'file': 'sample.txt', 'description': '测试数据'}],
                    'checks': [dict(id='c1', kind='text', prompt='填写测试结果', expected=EXPECTED_SECRET)],
                    'writeup': {'prompt': '写一段结论', 'rubric': ['说明结果', '指出局限']}})


def write_card(root, meta, body=None, track='alpha'):
    if body is None:
        body = ('## 讲解\n### 第一小节\n学习内容。\n\n### 第二小节\n更多内容。\n\n## 怎么学\n按顺序练习。\n'
                if meta['type'] == 'lesson' else
                '## 任务说明\n仅用于测试的任务说明。\n\n## 提示\n### 提示 1\n先检查输入。\n### 提示 2\n再检查转换。\n\n## 参考解法\n' + SOLUTION_SECRET + '\n')
    path = root / 'content/cards' / track / (meta['id'] + '.md')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('---\n' + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False) + '---\n' + body, encoding='utf-8')
    if meta['type'] == 'task':
        directory = root / 'data/tasks' / meta['id']
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'sample.txt').write_text('ACGT\n', encoding='utf-8')
        answers = root / 'data/generators' / (meta['id'] + '.answers.json')
        answers.parent.mkdir(parents=True, exist_ok=True)
        answers.write_text(json.dumps({'checks': {c['id']: c['expected'] for c in meta['checks']}}), encoding='utf-8')
    return path


def isolated_course(root):
    course = dict(spec_version=1, content_version='2026-10-03.4',
                  lab={'name': '测试课题组', 'fictional': True},
                  people=[dict(id='mentor', name='测试导师', role='测试负责人', initial='测'),
                          dict(id='peer', name='测试同学', role='测试研究生', initial='同')],
                  welcome={'from': 'mentor', 'message': '欢迎第一段。\n\n欢迎第二段。'},
                  tracks=[dict(id='alpha', label='A', title='测试主线', summary='测试课程',
                               cards=['lesson-one', 'task-one', 'lesson-two']),
                          dict(id='beta', label='B', title='测试第二线', summary='稍后开放', cards=[])])
    (root / 'content').mkdir(parents=True, exist_ok=True)
    (root / 'content/course.yaml').write_text(yaml.safe_dump(course, allow_unicode=True), encoding='utf-8')
    write_card(root, lesson_meta())
    write_card(root, task_meta())
    write_card(root, lesson_meta('lesson-two'))
    return course


def empty_progress():
    return {'spec_version': 2, 'cards': {}, 'events': [], 'pending_new_cards': []}


def done_record(at='2026-10-03T08:00:00+08:00', summary='我学会了测试。'):
    return {'status': 'done', 'started_at': at, 'updated_at': at, 'done_at': at,
            'summary': summary, 'ui': {'phase': 'wrap', 'anchor': 'summary', 'learn_completed': True}}


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='learning-hub-content-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.course = isolated_course(self.root)

    def assert_skipped(self, cid='lesson-one'):
        result = load_content(self.root)
        self.assertNotIn(cid, result.cards)
        self.assertTrue(any(i['card'] == cid for i in result.issues))
        self.assertIn('lesson-two', result.cards)
        return result

    def test_valid_course_populates_codes_phases_and_prerequisites(self):
        result = load_content(self.root)
        self.assertEqual(result.issues, [])
        self.assertEqual(result.all_ids, ['lesson-one', 'task-one', 'lesson-two'])
        self.assertEqual(result.cards['task-one']['code'], 'A2')
        self.assertEqual(result.cards['task-one']['prerequisites'], ['lesson-one'])
        self.assertEqual(result.cards['lesson-one']['headings'], ['第一小节', '第二小节'])
        self.assertEqual(len(result.cards['task-one']['hints']), 2)

    def test_missing_required_field_skips_only_bad_card(self):
        (self.root / 'content/cards/alpha/lesson-one.md').write_bytes((ERROR_FIXTURES / 'missing-field.md').read_bytes())
        self.assert_skipped()

    def test_wrong_heading_order_skips_only_bad_card(self):
        (self.root / 'content/cards/alpha/lesson-one.md').write_bytes((ERROR_FIXTURES / 'wrong-headings.md').read_bytes())
        self.assert_skipped()

    def test_broken_yaml_skips_only_bad_card_without_leaking_source(self):
        path = self.root / 'content/cards/alpha/lesson-one.md'
        path.write_bytes((ERROR_FIXTURES / 'broken-yaml.md').read_bytes())
        result = self.assert_skipped()
        self.assertNotIn(EXPECTED_SECRET, json.dumps(result.issues))

    def test_unknown_person_skips_task(self):
        (self.root / 'content/cards/alpha/task-one.md').write_bytes((ERROR_FIXTURES / 'unknown-person.md').read_bytes())
        self.assert_skipped('task-one')

    def test_code_fences_do_not_create_sections_subheadings_or_hints(self):
        text = '---\nid: sample\n---\n## 讲解\n```python\n## 假章节\n### 假小节\n```\n### 真实小节\n## 怎么学\n内容'
        meta, sections, titles = split_markdown(text)
        self.assertEqual(titles, ['讲解', '怎么学'])
        self.assertEqual(subheadings(sections['讲解']), ['真实小节'])
        self.assertEqual(split_hints('### 提示 1\n~~~\n### 提示 999\n~~~\n提示内容\n### 提示 2\n后续'),
                         ['~~~\n### 提示 999\n~~~\n提示内容', '后续'])

    def test_front_matter_dates_become_json_compatible(self):
        text = '---\nverified: 2026-10-03\n---\n## 讲解\n内容'
        self.assertEqual(split_markdown(text)[0]['verified'], '2026-10-03')

    def test_invalid_global_course_degrades_to_issue(self):
        (self.root / 'content/course.yaml').write_text('tracks: [broken', encoding='utf-8')
        result = load_content(self.root)
        self.assertEqual(result.cards, {})
        self.assertTrue(result.issues)

    def test_yaml_recursive_alias_is_reported_instead_of_crashing(self):
        (self.root / 'content/course.yaml').write_text('loop: &loop\n  self: *loop\n', encoding='utf-8')
        result = load_content(self.root)
        self.assertEqual(result.cards, {})
        self.assertTrue(result.issues)

    def test_unsafe_internal_reference_is_skipped_before_reading(self):
        from app.tests.test_updater import monitor_parent
        meta = task_meta()
        meta['data'] = [{'file': '../../../workspace/private.txt', 'description': 'bad'}]
        write_card(self.root, meta)
        with monitor_parent(self.root):
            self.assert_skipped('task-one')

    def test_symlink_data_is_rejected(self):
        data = self.root / 'data/tasks/task-one/sample.txt'
        data.unlink()
        symlink_or_skip(self, data, self.root / 'workspace/private.txt')
        self.assert_skipped('task-one')

    def test_cycles_and_unknown_dependencies_are_reported(self):
        meta = lesson_meta()
        meta['requires'] = ['lesson-two']
        write_card(self.root, meta)
        result = load_content(self.root)
        self.assertEqual(result.cards, {})
        self.assertEqual(len(result.issues), 3)
        meta['requires'] = ['unknown-card']
        write_card(self.root, meta)
        self.assert_skipped()

    def test_generator_mismatch_skips_task_without_echoing_answer(self):
        (self.root / 'data/generators/task-one.answers.json').write_text('{"checks":{"c1":"different"}}', encoding='utf-8')
        result = self.assert_skipped('task-one')
        self.assertNotIn(EXPECTED_SECRET, json.dumps(result.issues))

    def test_source_and_resource_urls_must_be_safe_absolute_http(self):
        for url in ('javascript:alert(1)', 'https://', 'https://example.test/\nunsafe'):
            with self.subTest(url=url):
                meta = task_meta()
                meta.update(simulated=False, source={'url': url, 'citation': '测试来源', 'license': 'test'})
                write_card(self.root, meta)
                self.assert_skipped('task-one')

    def test_runtime_rereads_content_on_each_load(self):
        self.assertEqual(load_content(self.root).cards['lesson-one']['title'], '测试学习卡 lesson-one')
        meta = lesson_meta()
        meta['title'] = '更新后的测试标题'
        write_card(self.root, meta)
        self.assertEqual(load_content(self.root).cards['lesson-one']['title'], '更新后的测试标题')

    def test_path_helper_rejects_non_content_roots(self):
        for value in ('workspace/x', 'progress/x', 'app/x', 'content/../../workspace/x', '/tmp/x', 'data\\x'):
            with self.subTest(path=value), self.assertRaises(ValueError):
                content_path(self.root, value)


class RetiredContentTests(unittest.TestCase):
    setUp = ContentTests.setUp
    # These test the real loading path using only a temporary course.
    def test_retired_middle_card_is_skipped_without_renumbering(self):
        meta = task_meta(); meta['retired'] = True
        write_card(self.root, meta)
        content = load_content(self.root)
        self.assertEqual(content.cards['lesson-two']['prerequisites'], ['lesson-one'])
        self.assertEqual(content.cards['lesson-two']['code'], 'A3')
        self.assertEqual(content.all_ids, ['lesson-one', 'task-one', 'lesson-two'])

    def test_all_retired_predecessors_and_explicit_retired_requires_are_skipped(self):
        for meta in (lesson_meta(), task_meta()):
            meta['retired'] = True; write_card(self.root, meta)
        meta = lesson_meta('lesson-two'); meta['requires'] = ['lesson-one', 'task-one']
        write_card(self.root, meta)
        content = load_content(self.root)
        self.assertEqual(content.cards['lesson-two']['prerequisites'], [])
        self.assertEqual(content.issues, [])

    def test_invalid_predecessor_is_not_mistaken_for_retired(self):
        (self.root / 'content/cards/alpha/task-one.md').write_text('broken', encoding='utf-8')
        content = load_content(self.root)
        self.assertEqual(content.cards['lesson-two']['prerequisites'], ['task-one'])
