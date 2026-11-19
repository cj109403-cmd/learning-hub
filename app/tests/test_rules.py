"""Business behavior from the contract; fixtures are in-memory invented courses."""

import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.server import rules
from app.server.migrations import new_progress, validate_progress


AT = "2026-10-15T09:00:00+08:00"


def lesson(cid, prerequisites=()):
    return {"id": cid, "type": "lesson", "title": "课程 " + cid,
            "verified": "2026-10-01", "prerequisites": list(prerequisites),
            "questions": [{"id": "q1", "prompt": "描述含义", "answer": "解释"},
                          {"id": "q2", "prompt": "预测输出", "answer": "输出"}],
            "resources": [{"title": "阅读材料"}, {"title": "练习材料"}]}


def task(cid, prerequisites=(), writeup=False):
    result = {"id": cid, "type": "task", "title": "任务 " + cid, "from": "mentor",
              "verified": "2026-10-01", "prerequisites": list(prerequisites),
              "checks": [{"id": "c1", "kind": "integer", "expected": 7, "prompt": "数目"},
                         {"id": "c2", "kind": "text", "expected": "AT", "prompt": "序列"}],
              "resources": []}
    if writeup:
        result["writeup"] = {"prompt": "写观察结论", "rubric": ["描述结果", "说明局限"]}
    return result


def course_fixture():
    cards = [lesson("a1"), lesson("a2", ["a1"]), task("a3", ["a2"]),
             lesson("b1"), task("b2", ["b1", "a2"], writeup=True),
             lesson("c1", ["b1"]), lesson("c2", ["c1"])]
    tracks = [{"id": "a", "title": "甲线", "cards": ["a1", "a2", "a3"]},
              {"id": "b", "title": "乙线", "cards": ["b1", "b2"]},
              {"id": "c", "title": "丙线", "cards": ["c1", "c2"]}]
    return SimpleNamespace(cards={card["id"]: card for card in cards},
                           all_ids=[card["id"] for card in cards],
                           course={"content_version": "test.2", "tracks": tracks})


class DateTests(unittest.TestCase):
    def test_missing_and_invalid_dates_have_a_timezone_aware_fallback(self):
        expected = datetime(1970, 1, 1, tzinfo=timezone.utc)
        for value in (None, "", "not-a-date", "2026-02-30T12:00:00+08:00", 0, {}):
            with self.subTest(value=value):
                self.assertEqual(rules.date(value), expected)
                self.assertLess(rules.date(value), rules.date(AT))

    def test_aware_dates_preserve_offsets_and_compare_absolute_times(self):
        earlier = rules.date("2026-10-15T10:00:00+08:00")
        later = rules.date("2026-10-15T06:00:00+03:00")
        self.assertEqual(earlier.utcoffset(), timedelta(hours=8))
        self.assertEqual(later.utcoffset(), timedelta(hours=3))
        self.assertLess(earlier, later)
        self.assertEqual(earlier, rules.date("2026-10-15T02:00:00+00:00"))

    def test_naive_dates_still_use_local_timezone(self):
        value = "2026-10-15T09:00:00"
        expected = datetime.fromisoformat(value).astimezone()
        self.assertEqual(rules.date(value), expected)
        self.assertEqual(rules.date(value).utcoffset(), expected.utcoffset())

    def test_unsupported_local_date_conversion_uses_safe_fallback(self):
        for error in (OSError(22, "Invalid argument"), OverflowError("date out of range")):
            with self.subTest(error=type(error).__name__):
                with patch("app.server.rules.datetime", wraps=datetime) as clock:
                    clock.fromisoformat.return_value = SimpleNamespace(
                        tzinfo=None, astimezone=Mock(side_effect=error))
                    self.assertEqual(rules.date("0001-01-01T00:00:00"),
                                     datetime(1970, 1, 1, tzinfo=timezone.utc))


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.content = course_fixture()
        self.progress = new_progress()

    def op(self, op, card=None, at=AT, **fields):
        # ProgressStore commits a copy only after a successful callback. Mirror
        # that transaction boundary instead of treating a failed op as saved.
        candidate = copy.deepcopy(self.progress)
        result = rules.perform(self.content, candidate, dict(fields, op=op, card=card), at=at)
        validate_progress(candidate)
        self.progress = candidate
        return result

    def done(self, cid, at="2026-10-01T09:00:00+08:00"):
        record = rules.default_record(self.content.cards[cid])
        record.update(status="done", started_at=at, updated_at=at, done_at=at, summary="保留总结")
        record["ui"]["learn_completed"] = True
        self.progress["cards"][cid] = record
        return record

    def recommendations(self, **kwargs):
        return rules.recommendations(self.content, self.progress, at=AT, **kwargs)

    def test_first_cards_and_implicit_previous_card_unlock(self):
        self.assertEqual(rules.status(self.content.cards["a1"], self.progress), "available")
        self.assertEqual(rules.status(self.content.cards["b1"], self.progress), "available")
        self.assertEqual(rules.status(self.content.cards["a2"], self.progress), "locked")
        self.done("a1")
        self.assertEqual(rules.status(self.content.cards["a2"], self.progress), "available")
        self.assertEqual(rules.status(self.content.cards["a3"], self.progress), "locked")

    def test_cross_track_prerequisites_must_all_be_done(self):
        self.done("b1")
        self.assertEqual(rules.status(self.content.cards["c1"], self.progress), "available")
        self.assertEqual(rules.status(self.content.cards["b2"], self.progress), "locked")
        self.done("a1")
        self.done("a2")
        self.assertEqual(rules.status(self.content.cards["b2"], self.progress), "available")

    def test_completed_card_remains_completed_when_content_gains_prerequisite(self):
        self.done("a1")
        self.content.cards["a1"]["prerequisites"] = ["b1"]
        self.assertEqual(rules.status(self.content.cards["a1"], self.progress), "done")

    def test_reading_rule_helpers_do_not_create_learning_records(self):
        before = copy.deepcopy(self.progress)
        card = self.content.cards["a1"]
        rules.record_for(card, self.progress)
        rules.missing(card, self.progress)
        rules.phase_status(card, self.progress)
        self.recommendations()
        self.assertEqual(self.progress, before)

    def test_summary_is_optional_by_default(self):
        for q in ("q1", "q2"):
            self.op("mark_question", "a1", q=q, mark="correct")
        self.assertEqual(self.op("complete", "a1"), {"completed": "a1"})
        self.assertEqual(self.progress["cards"]["a1"]["summary"], "")

    def test_lesson_needs_all_correct_and_nonempty_summary_but_not_answers_or_resources(self):
        self.op("mark_question", "a1", q="q1", mark="correct")
        self.op("save_summary", "a1", text="学到了变量")
        with self.assertRaises(rules.RuleError) as caught:
            self.op("complete", "a1")
        self.assertEqual(caught.exception.status, 422)
        self.assertIn("q2", str(caught.exception))
        self.op("mark_question", "a1", q="q2", mark="wrong")
        self.assertTrue(rules.missing(self.content.cards["a1"], self.progress))
        self.op("mark_question", "a1", q="q2", mark="correct")
        self.op("save_summary", "a1", text=" \n ")
        # CONTENT_SPEC v0.8: the summary is optional unless settings.json requires it.
        self.assertEqual(rules.missing(self.content.cards["a1"], self.progress), [])
        self.assertEqual([item["anchor"] for item in rules.missing(self.content.cards["a1"], self.progress, require_summary=True)], ["summary"])
        token = rules.SUMMARY_REQUIRED.set(True)
        try:
            with self.assertRaises(rules.RuleError):
                self.op("complete", "a1")
        finally:
            rules.SUMMARY_REQUIRED.reset(token)
        self.op("save_summary", "a1", text="完整总结")
        self.assertEqual(self.op("complete", "a1"), {"completed": "a1"})
        record = self.progress["cards"]["a1"]
        self.assertEqual(record["resources_done"], [])
        self.assertEqual(record["questions"]["q1"]["my_answer"], "")
        self.assertEqual(record["content_verified_at_completion"], "2026-10-01")

    def test_task_writeup_requires_text_each_rubric_all_checks_and_summary(self):
        self.done("b1")
        self.done("a2")
        self.op("check", "b2", c="c1", value="7")
        self.op("save_summary", "b2", text="总结")
        anchors = {item["anchor"] for item in rules.missing(self.content.cards["b2"], self.progress)}
        self.assertEqual(anchors, {"c2", "writeup", "rubric-0", "rubric-1"})
        self.op("check", "b2", c="c2", value="AT")
        self.op("save_writeup", "b2", text=" \n")
        self.op("toggle_rubric", "b2", index=0)
        with self.assertRaises(rules.RuleError):
            self.op("complete", "b2")
        self.op("save_writeup", "b2", text="结果是……局限是……")
        self.op("toggle_rubric", "b2", index=1)
        self.assertEqual(rules.missing(self.content.cards["b2"], self.progress), [])
        self.op("complete", "b2")
        self.assertEqual(self.progress["cards"]["b2"]["status"], "done")

    def test_task_without_writeup_has_no_writeup_requirement_or_record(self):
        self.done("a2")
        self.op("check", "a3", c="c1", value="7")
        self.op("check", "a3", c="c2", value="AT")
        self.op("save_summary", "a3", text="检查完成")
        self.op("complete", "a3")
        self.assertNotIn("writeup", self.progress["cards"]["a3"])

    def test_learn_completed_is_monotonic_and_free_navigation_does_not_mark_it(self):
        self.op("set_ui", "a1", phase="practice", anchor="q2")
        self.assertFalse(self.progress["cards"]["a1"]["ui"]["learn_completed"])
        self.op("set_ui", "a1", phase="learn", anchor="end", learn_completed=True)
        self.op("set_ui", "a1", phase="wrap", anchor="summary")
        self.assertTrue(self.progress["cards"]["a1"]["ui"]["learn_completed"])
        for invalid in (False, 0, "true", None):
            with self.subTest(invalid=invalid):
                with self.assertRaises(rules.RuleError):
                    self.op("set_ui", "a1", phase="learn", anchor="top", learn_completed=invalid)
                self.assertTrue(self.progress["cards"]["a1"]["ui"]["learn_completed"])

    def test_phase_status_is_independent_from_free_navigation(self):
        self.op("set_ui", "a1", phase="wrap", anchor="summary")
        self.assertEqual(rules.phase_status(self.content.cards["a1"], self.progress), {"learn": False, "practice": False, "wrap": False})
        for q in ("q1", "q2"):
            self.op("mark_question", "a1", q=q, mark="correct")
        self.assertEqual(rules.phase_status(self.content.cards["a1"], self.progress), {"learn": False, "practice": True, "wrap": False})
        self.op("save_summary", "a1", text="总结")
        self.op("complete", "a1")
        self.assertEqual(rules.phase_status(self.content.cards["a1"], self.progress), {"learn": False, "practice": True, "wrap": True})

    def test_first_visit_seeds_known_cards_but_marks_none_new(self):
        result = self.op("visit")
        self.assertEqual(result["new_cards"], [])
        self.assertEqual(self.progress["known_cards"], self.content.all_ids)
        self.assertEqual(self.progress["pending_new_cards"], [])
        self.assertEqual(self.progress["seen_content_version"], "test.2")
        self.assertEqual(self.progress["last_visit_at"], AT)

    def test_visit_unions_pending_before_updating_known_and_prunes_retired_missing(self):
        self.progress.update(known_cards=["a1", "a2", "a3", "b1"], pending_new_cards=["a2", "missing", "a3"],
                             last_visit_at="2026-10-01T08:00:00+08:00", seen_content_version="test.1")
        self.content.cards["a3"]["retired"] = True
        result = self.op("visit")
        self.assertEqual(result["previous_visit_at"], "2026-10-01T08:00:00+08:00")
        self.assertEqual(result["previous_content_version"], "test.1")
        self.assertEqual(result["new_cards"], ["b2", "c1", "c2"])
        self.assertEqual(self.progress["pending_new_cards"], ["a2", "b2", "c1", "c2"])
        self.assertEqual(self.progress["known_cards"], self.content.all_ids)
        self.assertEqual(self.op("visit")["new_cards"], [])
        self.assertEqual(self.progress["pending_new_cards"], ["a2", "b2", "c1", "c2"])

    def test_opening_locked_card_only_removes_new_label(self):
        self.progress["pending_new_cards"] = ["a2", "b2"]
        self.op("seen_card", "a2")
        self.assertEqual(self.progress["pending_new_cards"], ["b2"])
        self.assertEqual(self.progress["cards"], {})
        self.assertEqual(self.progress["events"], [])
        self.op("seen_card", "a2")
        self.assertEqual(self.progress["pending_new_cards"], ["b2"])

    def test_locked_card_rejects_every_other_operation(self):
        for op in ("start_card", "set_ui", "toggle_resource", "save_answer", "mark_question", "check",
                   "view_solution", "save_writeup", "toggle_rubric", "save_summary", "complete"):
            with self.subTest(op=op):
                with self.assertRaises(rules.RuleError) as caught:
                    self.op(op, "a2")
                self.assertEqual(caught.exception.status, 403)
        self.assertEqual(self.progress["cards"], {})

    def test_start_is_idempotent_and_preserves_original_time(self):
        self.op("start_card", "a1", at="2026-10-01T09:00:00+08:00")
        self.op("start_card", "a1", at=AT)
        record = self.progress["cards"]["a1"]
        self.assertEqual(record["started_at"], "2026-10-01T09:00:00+08:00")
        self.assertEqual(record["updated_at"], record["started_at"])
        self.assertEqual(len(self.progress["events"]), 1)

    def test_answer_and_summary_edits_do_not_erase_done_status_or_history(self):
        record = self.done("a1")
        original_done_at = record["done_at"]
        self.op("save_answer", "a1", q="q1", text="新的回答")
        self.op("mark_question", "a1", q="q1", mark="wrong")
        self.op("save_summary", "a1", text="修订总结")
        self.op("start_card", "a1")
        record = self.progress["cards"]["a1"]
        self.assertEqual(record["status"], "done")
        self.assertEqual(record["done_at"], original_done_at)
        self.assertEqual(record["questions"]["q1"]["my_answer"], "新的回答")
        self.assertEqual(record["summary"], "修订总结")

    def test_check_attempts_store_time_and_result_without_returning_expected(self):
        self.done("a2")
        wrong = self.op("check", "a3", c="c1", value="6")
        right = self.op("check", "a3", c="c1", value="７％")
        self.assertEqual(wrong, {"correct": False, "attempts": 1})
        self.assertEqual(right, {"correct": True, "attempts": 2})
        attempts = self.progress["cards"]["a3"]["checks"]["c1"]["attempts"]
        self.assertEqual([a["value"] for a in attempts], ["6", "７％"])
        self.assertEqual([a["at"] for a in attempts], [AT, AT])
        self.assertNotIn("expected", attempts[0])

    def test_solution_needs_three_wrong_attempts_on_the_same_check(self):
        self.done("a2")
        self.op("check", "a3", c="c1", value="0")
        self.op("check", "a3", c="c1", value="0")
        self.op("check", "a3", c="c2", value="wrong")
        self.assertFalse(rules.solution_unlocked(self.content.cards["a3"], self.progress))
        with self.assertRaises(rules.RuleError) as caught:
            self.op("view_solution", "a3")
        self.assertEqual(caught.exception.status, 403)
        self.op("check", "a3", c="c1", value="7")
        self.assertFalse(rules.solution_unlocked(self.content.cards["a3"], self.progress))
        self.op("check", "a3", c="c1", value="0")
        self.assertTrue(rules.solution_unlocked(self.content.cards["a3"], self.progress))
        self.op("view_solution", "a3")
        self.assertTrue(self.progress["cards"]["a3"]["solution_viewed"])
        self.assertEqual(self.progress["events"][-1]["type"], "solution_viewed")

    def test_done_task_unlocks_solution_without_wrong_attempts(self):
        self.done("a3")
        self.assertTrue(rules.solution_unlocked(self.content.cards["a3"], self.progress))
        self.assertFalse(rules.solution_unlocked(self.content.cards["a1"], self.progress))
        self.op("view_solution", "a3")

    def test_resource_and_rubric_toggles_reject_out_of_bounds_and_bool(self):
        self.op("toggle_resource", "a1", index=1)
        self.assertEqual(self.progress["cards"]["a1"]["resources_done"], [1])
        self.op("toggle_resource", "a1", index=1)
        self.assertEqual(self.progress["cards"]["a1"]["resources_done"], [])
        for index in (-1, 2, True, "0"):
            with self.subTest(index=index):
                with self.assertRaises(rules.RuleError):
                    self.op("toggle_resource", "a1", index=index)
        self.done("a2")
        self.done("b1")
        self.op("toggle_rubric", "b2", index=0)
        self.op("toggle_rubric", "b2", index=0)
        self.assertEqual(self.progress["cards"]["b2"]["writeup"]["rubric_checked"], [])

    def test_successive_complete_does_not_duplicate_event_or_change_done_time(self):
        for q in ("q1", "q2"):
            self.op("mark_question", "a1", q=q, mark="correct")
        self.op("save_summary", "a1", text="总结")
        self.op("complete", "a1", at="2026-10-01T09:00:00+08:00")
        self.op("complete", "a1", at=AT)
        self.assertEqual(self.progress["cards"]["a1"]["done_at"], "2026-10-01T09:00:00+08:00")
        self.assertEqual(sum(event["type"] == "card_done" for event in self.progress["events"]), 1)

    def test_welcome_seen_time_is_not_reset(self):
        self.op("welcome_done", at="2026-10-01T09:00:00+08:00")
        self.op("welcome_done", at=AT)
        self.assertEqual(self.progress["welcome_seen_at"], "2026-10-01T09:00:00+08:00")

    def test_recommendations_continue_most_recent_only_once_and_limit_three(self):
        self.content.cards["c1"]["prerequisites"] = []
        self.op("start_card", "a1", at="2026-10-01T09:00:00+08:00")
        self.op("start_card", "b1", at="2026-10-02T09:00:00+08:00")
        recommended = self.recommendations()
        self.assertEqual(recommended[0][0]["id"], "b1")
        self.assertEqual(recommended[0][1], "上次学到一半")
        self.assertEqual([card["id"] for card, _ in recommended], ["b1", "a1", "c1"])
        self.assertEqual(len({card["id"] for card, _ in recommended}), len(recommended))

    def test_continue_uses_absolute_time_and_tolerates_missing_or_invalid_time(self):
        self.op("start_card", "a1", at="2026-10-15T10:00:00+08:00")
        self.op("start_card", "b1", at="2026-10-15T06:00:00+03:00")
        self.assertEqual(self.recommendations()[0][0]["id"], "b1")
        for value in (None, "invalid"):
            with self.subTest(value=value):
                self.progress["cards"]["b1"]["updated_at"] = value
                self.assertEqual(self.recommendations()[0][0]["id"], "a1")

    def test_recommendation_ties_follow_course_order_deterministically(self):
        self.content.cards["c1"]["prerequisites"] = []
        self.assertEqual([card["id"] for card, _ in self.recommendations()], ["a1", "b1", "c1"])
        self.assertTrue(all(reason == "还没开始这条线" for _, reason in self.recommendations()))
        self.content.course["tracks"].reverse()
        self.assertEqual([card["id"] for card, _ in self.recommendations()], ["c1", "b1", "a1"])

    def test_recommendations_sort_never_finished_then_oldest_completion(self):
        self.content.cards["c1"]["prerequisites"] = []
        self.done("a1", at="2026-10-14T09:00:00+08:00")
        self.done("c1", at="2026-10-01T09:00:00+08:00")
        recommended = self.recommendations()
        self.assertEqual([card["id"] for card, _ in recommended], ["b1", "c2", "a2"])
        self.assertEqual([reason for _, reason in recommended], ["还没开始这条线", "这条线上次学习是 14 天前", "这条线的下一节"])

    def test_new_unlock_reason_uses_latest_prerequisite_and_overrides_older_reason(self):
        self.done("a1", at="2026-10-01T09:00:00+08:00")
        self.done("a2", at="2026-10-03T09:00:00+08:00")
        self.done("b1", at="2026-10-05T09:00:00+08:00")
        self.assertEqual(rules.unlock_time(self.content.cards["b2"], self.progress), "2026-10-05T09:00:00+08:00")
        recommended = dict((card["id"], reason) for card, reason in self.recommendations(previous_visit="2026-10-04T09:00:00+08:00"))
        self.assertEqual(recommended["b2"], "完成「课程 b1」后解锁")
        self.assertEqual(recommended["c1"], "完成「课程 b1」后解锁")

    def test_retired_cards_are_not_recommended_but_completion_history_affects_sort(self):
        self.content.cards["c1"]["prerequisites"] = []
        self.done("a1", at="2026-10-14T09:00:00+08:00")
        self.content.cards["a1"]["retired"] = True
        self.done("c1", at="2026-10-01T09:00:00+08:00")
        recommended = self.recommendations()
        self.assertEqual([card["id"] for card, _ in recommended], ["b1", "c2", "a2"])
        self.assertEqual(recommended[-1][1], "这条线的下一节")

    def test_all_done_or_empty_content_yields_no_recommendation(self):
        for cid in self.content.all_ids:
            self.done(cid)
        self.assertEqual(self.recommendations(), [])
        self.assertEqual(rules.recommendations(SimpleNamespace(cards={}, all_ids=[], course={"tracks": []}), new_progress(), at=AT), [])

    def test_unknown_card_and_invalid_ops_are_rejected(self):
        with self.assertRaises(rules.RuleError) as caught:
            self.op("start_card", "missing")
        self.assertEqual(caught.exception.status, 404)
        for fields in ({"op": "unknown"}, {"op": "save_answer", "q": "missing", "text": "x"},
                       {"op": "save_summary", "text": None}, {"op": "mark_question", "q": "q1", "mark": "maybe"},
                       {"op": "set_ui", "phase": "unknown", "anchor": "x"}):
            with self.subTest(fields=fields):
                with self.assertRaises(rules.RuleError):
                    self.op(card="a1", **fields)


if __name__ == "__main__":
    unittest.main()
