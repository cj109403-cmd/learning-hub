from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from app.server.content import load_content
from app.server import export, rules
from app.tests.test_content import (isolated_course, empty_progress, done_record,
                                    EXPECTED_SECRET, SOLUTION_SECRET)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='learning-hub-export-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        isolated_course(self.root)
        self.content = load_content(self.root)
        self.progress = empty_progress()

    def unlock_task(self):
        self.progress['cards']['lesson-one'] = done_record(summary='变量保存一个值')

    def assert_no_secrets(self, text):
        self.assertNotIn(EXPECTED_SECRET, text)
        self.assertNotIn(SOLUTION_SECRET, text)

    def test_empty_record_has_strict_six_section_format(self):
        text = export.record_text(self.content, self.progress, '2026-10-15T21:30:00+08:00')
        self.assertEqual(text, '# 学习记录摘要（导出于 2026-10-15 21:30）\n\n'
                         '## 各条线进度\n- A 测试主线：完成 0 / 3\n- B 测试第二线：完成 0 / 0\n\n'
                         '## 最近完成（最多 10 条，新的在前）\n- 无\n\n'
                         '## 自测题里标记"没答对"、还没改过来的\n- 无\n\n'
                         '## 任务里答错 3 次以上的题\n- 无\n\n'
                         '## 查看过参考解法的任务\n- 无\n\n'
                         '## 进行中但超过 14 天没动的卡\n- 无\n')

    def test_record_lists_completed_wrong_attempts_solution_and_stale(self):
        self.unlock_task()
        self.progress['cards']['lesson-one']['questions'] = {'q1': {'self_mark': 'wrong'}}
        self.progress['cards']['task-one'] = {
            'status': 'in_progress', 'updated_at': '2026-09-01T10:00:00+08:00', 'solution_viewed': True,
            'checks': {'c1': {'attempts': [{'value': 'wrong', 'correct': False}] * 3 + [{'value': 'my result', 'correct': True}]}}}
        text = export.record_text(self.content, self.progress, '2026-10-15T21:30:00+08:00')
        self.assertIn('- 10-03 A1 测试学习卡 lesson-one："变量保存一个值"', text)
        self.assertIn('完成 1 / 3；进行中：A2 测试任务 task-one', text)
        self.assertIn('A1 测试学习卡 lesson-one q1：预测结果？', text)
        self.assertIn('A2 测试任务 task-one c1：已尝试 4 次，已答对', text)
        self.assertIn('## 查看过参考解法的任务\n- A2 测试任务 task-one', text)
        self.assertIn('## 进行中但超过 14 天没动的卡\n- A2 测试任务 task-one', text)
        self.assert_no_secrets(text)

    def test_three_wrong_attempts_required_not_three_total(self):
        self.unlock_task()
        self.progress['cards']['task-one'] = {'checks': {'c1': {'attempts': [{'correct': False}, {'correct': False}, {'correct': True}]}}}
        text = export.record_text(self.content, self.progress)
        self.assertIn('## 任务里答错 3 次以上的题\n- 无', text)

    def test_help_for_lesson_contains_headings_not_hidden_solutions(self):
        text = export.ai_text(self.content, self.progress, {'kind': 'help', 'card': 'lesson-one', 'problem': '不理解变量', 'code': ''})
        self.assertIn('【我的代码或报错】\n无', text)
        self.assertIn('【相关内容】\n- 第一小节\n- 第二小节', text)
        self.assertIn('不理解变量', text)
        self.assert_no_secrets(text)

    def test_help_task_uses_first_600_chars_and_latest_user_answer(self):
        self.unlock_task()
        self.content.cards['task-one']['sections']['任务说明'] = '测' * 600 + 'TRUNCATION_MARKER'
        self.progress['cards']['task-one'] = {'checks': {'c1': {'attempts': [{'value': 'old'}, {'value': 'latest-user-answer'}]}}}
        text = export.ai_text(self.content, self.progress, {'kind': 'help', 'card': 'task-one', 'problem': '读不懂', 'code': 'print(1)'})
        self.assertNotIn('TRUNCATION_MARKER', text)
        self.assertIn('我最近的回答：latest-user-answer', text)
        self.assertIn('c1：填写测试结果', text)
        self.assert_no_secrets(text)

    def test_question_export_has_reference_answer_and_missing_answer_fallback(self):
        text = export.ai_text(self.content, self.progress, {'kind': 'question', 'card': 'lesson-one', 'q': 'q1'})
        self.assertIn('【我的回答】我还没写，请先给我一点思路', text)
        self.assertIn('【参考答案】参考解释一', text)
        self.progress['cards']['lesson-one'] = {'questions': {'q1': {'my_answer': '我的想法'}}}
        text = export.ai_text(self.content, self.progress, {'kind': 'question', 'card': 'lesson-one', 'q': 'q1'})
        self.assertIn('【我的回答】我的想法', text)

    def test_writeup_export_contains_prompt_rubric_and_own_text(self):
        self.unlock_task()
        self.progress['cards']['task-one'] = {'writeup': {'text': '我的结论', 'rubric_checked': []}}
        text = export.ai_text(self.content, self.progress, {'kind': 'writeup', 'card': 'task-one'})
        self.assertIn('【要求】写一段结论', text)
        self.assertIn('1. 说明结果\n2. 指出局限', text)
        self.assertIn('【我写的内容】\n我的结论', text)
        self.assert_no_secrets(text)

    def test_locked_or_unknown_card_export_is_rejected(self):
        for cid in ('task-one', 'unknown'):
            with self.subTest(card=cid), self.assertRaises(rules.RuleError):
                export.ai_text(self.content, self.progress, {'kind': 'help', 'card': cid})

    def test_record_saved_under_temporary_exports_matches_returned_text(self):
        directory = self.root / 'fake-progress'
        directory.mkdir()
        result = export.save_record(directory, self.content, self.progress)
        path = Path(result['path'])
        self.assertEqual(path.parent, directory / 'exports')
        self.assertRegex(path.name, r'^学习记录-\d{8}-\d{4}\.md$')
        self.assertEqual(path.read_text(encoding='utf-8'), result['text'])
        self.assert_no_secrets(result['text'])
