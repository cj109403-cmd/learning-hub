from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.server.content import load_content
from app.server import rules, views
from app.tests.test_content import (isolated_course, empty_progress, done_record,
                                    task_meta, write_card, EXPECTED_SECRET, SOLUTION_SECRET, yaml)


class ViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='learning-hub-views-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        isolated_course(self.root)
        self.content = load_content(self.root)
        self.progress = empty_progress()

    def done(self, cid, at='2026-10-03T08:00:00+08:00'):
        self.progress['cards'][cid] = done_record(at, '总结 ' + cid)

    def test_fresh_today_has_first_card_and_members_fallback(self):
        result = views.today(self.content, self.progress, {})
        self.assertEqual(result['sentence'], '从第一张卡开始吧。')
        self.assertEqual(result['main']['id'], 'lesson-one')
        self.assertIsNone(result['latest_message'])
        self.assertEqual(result['recent'], [])

    def test_today_ongoing_has_priority_over_newly_unlocked_and_new_content(self):
        self.done('lesson-one')
        self.progress['cards']['task-one'] = {'status': 'in_progress', 'updated_at': '2026-10-03T09:00:00+08:00'}
        result = views.today(self.content, self.progress, {'previous_visit_at': '2026-10-03T07:00:00+08:00',
                                                         'previous_content_version': 'old', 'new_cards': ['lesson-two']})
        self.assertIn('上次你学到', result['sentence'])
        self.assertIn('任务还差 1 题', result['sentence'])
        self.assertIsNone(result['latest_message'])

    def test_today_newly_unlocked_task_precedes_new_content(self):
        self.done('lesson-one')
        result = views.today(self.content, self.progress, {'previous_visit_at': '2026-10-03T07:00:00+08:00',
                                                         'previous_content_version': 'old', 'new_cards': ['lesson-two']})
        self.assertEqual(result['sentence'], '测试导师给你发了一个新任务。')

    def test_today_new_content_precedes_last_completed(self):
        self.done('lesson-one')
        result = views.today(self.content, self.progress, {'previous_content_version': 'old', 'new_cards': ['lesson-two']})
        self.assertEqual(result['sentence'], '新加了 1 张卡。')

    def test_today_zero_added_skips_new_content_sentence(self):
        self.done('lesson-one')
        result = views.today(self.content, self.progress, {'previous_content_version': 'old', 'new_cards': []})
        self.assertEqual(result['sentence'], '上次完成了「测试学习卡 lesson-one」。下一张是「测试任务 task-one」。')

    def test_today_all_complete(self):
        for cid in self.content.all_ids:
            self.done(cid)
        result = views.today(self.content, self.progress, {})
        self.assertEqual(result['sentence'], '这一阶段全部完成了。')
        self.assertIsNone(result['main'])

    def test_today_remaining_writeup_and_summary_are_specific(self):
        self.done('lesson-one')
        self.progress['cards']['task-one'] = {'status': 'in_progress', 'checks': {'c1': {'attempts': [{'correct': True}]}},
                                              'updated_at': '2026-10-03T09:00:00+08:00'}
        self.assertIn('文字结论', views.today(self.content, self.progress, {})['sentence'])
        self.progress['cards']['task-one']['writeup'] = {'text': '已写结论', 'rubric_checked': [0, 1]}
        self.assertIn('可以完成这一节了', views.today(self.content, self.progress, {})['sentence'])
        token = rules.SUMMARY_REQUIRED.set(True)
        try:
            self.assertIn('只差写总结', views.today(self.content, self.progress, {})['sentence'])
        finally:
            rules.SUMMARY_REQUIRED.reset(token)

    def test_unlocked_solution_requires_explicit_view_before_payload(self):
        self.done('lesson-one')
        self.progress['cards']['task-one'] = {'checks': {'c1': {'attempts': [{'correct': False}] * 3}}}
        card = self.content.cards['task-one']
        public = views.public_card(self.content, card, self.progress)
        self.assertTrue(public['solution_unlocked'])
        self.assertNotIn(SOLUTION_SECRET, json.dumps(public))
        self.progress['cards']['task-one']['solution_viewed'] = True
        public = views.public_card(self.content, card, self.progress)
        self.assertIn(SOLUTION_SECRET, json.dumps(public))
        self.assertNotIn(EXPECTED_SECRET, json.dumps(public))
        self.assertNotIn('expected', public['checks'][0])

    def test_locked_card_never_exposes_solution_even_with_old_view_flag(self):
        self.progress['cards']['task-one'] = {'solution_viewed': True, 'checks': {'c1': {'attempts': [{'correct': False}] * 3}}}
        public = views.public_card(self.content, self.content.cards['task-one'], self.progress)
        self.assertFalse(public['solution_unlocked'])
        self.assertNotIn(SOLUTION_SECRET, json.dumps(public))

    def test_lab_order_task_then_summary_then_debrief(self):
        self.done('lesson-one', '2026-10-03T08:00:00+08:00')
        self.done('task-one', '2026-10-03T09:00:00+08:00')
        result = views.lab(self.content, self.progress)
        messages = result['people'][0]['messages']
        self.assertEqual([m['kind'] for m in messages], ['task', 'summary', 'debrief'])
        self.assertEqual(messages[0]['at'], '2026-10-03T08:00:00+08:00')
        self.assertEqual(messages[1]['at'], messages[2]['at'])
        self.assertIn('\n\n', messages[0]['text'])
        self.assertFalse(result['people'][0]['unread'])

    def test_done_without_summary_has_no_empty_bubble_and_shows_in_notes(self):
        self.done('lesson-one')
        self.done('task-one')
        self.progress['cards']['task-one']['summary'] = ''
        kinds = [m['kind'] for m in views.lab(self.content, self.progress)['people'][0]['messages']]
        self.assertNotIn('summary', kinds)
        self.assertEqual(kinds[0], 'task')
        notes = views.journal(self.content, self.progress, view='notes')
        self.assertIn(('task-one', ''), [(n['card']['id'], n['text']) for n in notes['notes']])
        self.assertNotIn('task-one', notes['copy_text'])
        self.assertNotIn('task-one', [c['id'] for c in views.today(self.content, self.progress, {})['recent']])

    def test_lab_locked_tasks_hidden_and_new_available_task_unread(self):
        self.assertEqual(views.lab(self.content, self.progress)['people'][0]['messages'], [])
        self.done('lesson-one')
        self.assertTrue(views.lab(self.content, self.progress)['people'][0]['unread'])

    def test_journal_notes_keep_course_order_timeline_keeps_date_order(self):
        self.done('lesson-one', '2026-10-03T08:00:00+08:00')
        self.done('task-one', '2026-10-03T10:00:00+08:00')
        result = views.journal(self.content, self.progress)
        self.assertEqual([x['card']['id'] for x in result['notes']], ['lesson-one', 'task-one'])
        self.assertEqual([x['card']['id'] for x in result['entries']], ['task-one', 'lesson-one'])
        self.assertIn('A1', result['copy_text'])
        self.assertEqual(views.journal(self.content, self.progress, track='beta')['notes'], [])

    def test_people_data_is_used_without_fixed_names(self):
        self.done('lesson-one')
        self.content.course['people'][0].update(name='更名后的导师', initial='更')
        message = views.today(self.content, self.progress, {'previous_visit_at': '2026-10-03T07:00:00+08:00'})
        self.assertEqual(message['sentence'], '更名后的导师给你发了一个新任务。')
        self.assertEqual(views.lab(self.content, self.progress)['people'][0]['initial'], '更')

    def test_new_track_task_appears_without_frontend_hardcoding(self):
        course = self.content.course
        course['tracks'][1]['cards'] = ['task-two']
        (self.root / 'content/course.yaml').write_text(yaml.safe_dump(course, allow_unicode=True), encoding='utf-8')
        meta = task_meta('task-two')
        meta['from'] = 'peer'
        write_card(self.root, meta, track='beta')
        self.content = load_content(self.root)
        result = views.today(self.content, self.progress, {})
        self.assertEqual(result['main']['id'], 'lesson-one')
        self.assertEqual(result['others'][0]['code'], 'B1')
        self.assertEqual(result['latest_message']['person']['id'], 'peer')
        self.assertEqual(result['latest_message']['card']['id'], 'task-two')

    def test_route_search_and_list_omit_retired_cards_and_secrets(self):
        self.content.cards['lesson-two']['retired'] = True
        outputs = [views.route(self.content, self.progress, {}), views.search_index(self.content, self.progress),
                   views.card_list(self.content, self.progress), views.today(self.content, self.progress, {})]
        for output in outputs:
            serialized = json.dumps(output)
            self.assertNotIn(EXPECTED_SECRET, serialized)
            self.assertNotIn(SOLUTION_SECRET, serialized)
        self.assertEqual(len(views.card_list(self.content, self.progress)), 2)
        self.assertNotIn('lesson-two', [x['id'] for x in views.search_index(self.content, self.progress)['entries']])

    def test_presentation_does_not_mutate_content_or_progress(self):
        old_content = deepcopy(self.content)
        old_progress = deepcopy(self.progress)
        for action in (lambda: views.today(self.content, self.progress, {}),
                       lambda: views.route(self.content, self.progress, {}),
                       lambda: views.lab(self.content, self.progress),
                       lambda: views.journal(self.content, self.progress)):
            action()
        self.assertEqual(self.content, old_content)
        self.assertEqual(self.progress, old_progress)


class RetiredViewsTests(unittest.TestCase):
    setUp = ViewTests.setUp
    done = ViewTests.done

    def retire_task(self):
        meta = task_meta(); meta['retired'] = True
        write_card(self.root, meta)
        self.content = load_content(self.root)

    def test_completed_retired_card_remains_readonly_in_journal_and_export(self):
        from app.server.export import record_text
        self.done('lesson-one'); self.done('task-one')
        self.retire_task()
        journal = views.journal(self.content, self.progress)
        historical = next(entry['card'] for entry in journal['entries'] if entry['card']['id'] == 'task-one')
        self.assertTrue(historical['retired']); self.assertTrue(historical['read_only'])
        self.assertFalse(historical['unlocked'])
        self.assertEqual(historical['status'], 'done')
        self.assertEqual(historical['code'], 'A2')
        self.assertIn('总结 task-one', journal['copy_text'])
        exported = record_text(self.content, self.progress)
        self.assertIn('完成 1 / 2', exported)
        self.assertIn('A2 测试任务 task-one', exported.split('## 最近完成')[1].split('## 自测题')[0])

    def test_unfinished_retired_card_does_not_enter_journal_or_recommendations(self):
        self.progress['cards']['task-one'] = {'status': 'in_progress', 'summary': '旧草稿', 'updated_at': '2026-10-01T12:00:00+08:00'}
        self.retire_task()
        before = deepcopy(self.progress)
        self.assertFalse(views.journal(self.content, self.progress)['entries'])
        today = views.today(self.content, self.progress, {})
        self.assertEqual(today['main']['id'], 'lesson-one')
        self.assertEqual(self.progress, before)
        self.assertNotIn('task-one', views.public_course(self.content)['tracks'][0]['cards'])

    def test_progress_and_completion_position_ignore_retired_card_but_keep_code(self):
        self.done('lesson-one'); self.done('task-one'); self.done('lesson-two')
        self.retire_task()
        track = views.tracks(self.content, self.progress)[0]
        self.assertEqual((track['done'], track['total']), (2, 2))
        completion = views.completion(self.content, self.progress, 'lesson-two', {})
        self.assertEqual(completion['position']['index'], 2)
        self.assertEqual(completion['position']['total'], 2)
        self.assertEqual(completion['card']['code'], 'A3')
        self.assertEqual(views.today(self.content, self.progress, {})['sentence'], '这一阶段全部完成了。')

    def test_retired_card_cannot_receive_any_learning_mutation(self):
        from app.server.rules import perform, RuleError
        self.done('task-one'); self.retire_task()
        before = deepcopy(self.progress)
        for op in ('start_card', 'set_ui', 'save_answer', 'check', 'save_summary', 'complete', 'view_solution'):
            with self.subTest(op=op), self.assertRaisesRegex(RuleError, '退休'):
                perform(self.content, self.progress, {'op': op, 'card': 'task-one'})
        self.assertEqual(self.progress, before)

    def test_retired_prerequisite_is_ignored_whether_completed_or_not(self):
        from app.server.rules import status
        self.done('lesson-one'); self.retire_task()
        self.assertEqual(status(self.content.cards['lesson-two'], self.progress), 'available')
        self.done('task-one')
        self.assertEqual(status(self.content.cards['lesson-two'], self.progress), 'available')
