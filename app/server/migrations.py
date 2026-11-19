"""Lossless progress migrations defined by CONTENT_SPEC §6.1.

Course content has its own spec_version; this module handles learning records only.
"""

from copy import deepcopy


CURRENT_VERSION = 2


class MigrationError(ValueError):
    status = 503


class UnsupportedVersion(MigrationError):
    def __init__(self, version):
        self.version = version
        super().__init__("这份学习记录使用更新的格式（版本 %s），请更新程序后再打开；当前程序不会写入或降级。" % version)


def new_progress():
    """Absence of known_cards identifies the very first visit (not an empty list)."""
    return {"spec_version": CURRENT_VERSION, "pending_new_cards": [], "cards": {}, "events": []}


def _typed(value, expected, label):
    if not isinstance(value, expected):
        raise MigrationError("学习记录的 %s 格式不正确。" % label)


def validate_progress(progress):
    """Check known container types without stripping unknown or retired records."""
    _typed(progress, dict, "根对象")
    version = progress.get("spec_version")
    if type(version) is not int or version < 1:
        raise MigrationError("学习记录缺少有效的 spec_version。")
    if version > CURRENT_VERSION:
        raise UnsupportedVersion(version)
    _typed(progress.get("cards"), dict, "cards")
    _typed(progress.get("events"), list, "events")
    for field in ("known_cards", "pending_new_cards"):
        if field in progress:
            _typed(progress[field], list, field)
            if any(not isinstance(item, str) for item in progress[field]):
                raise MigrationError("学习记录的 %s 必须是卡片 id 列表。" % field)
    if version == 2 and "pending_new_cards" not in progress:
        raise MigrationError("版本 2 的学习记录缺少 pending_new_cards。")
    for field in ("welcome_seen_at", "last_visit_at", "seen_content_version"):
        if field in progress and progress[field] is not None:
            _typed(progress[field], str, field)
    for card_id, card in progress["cards"].items():
        if not isinstance(card_id, str) or not isinstance(card, dict):
            raise MigrationError("学习记录的卡片格式不正确。")
        if "status" in card and card["status"] not in ("in_progress", "done"):
            raise MigrationError("学习记录的卡片状态不正确。")
        for field in ("questions", "checks", "writeup", "ui"):
            if field in card:
                _typed(card[field], dict, "%s.%s" % (card_id, field))
        if "resources_done" in card:
            _typed(card["resources_done"], list, "%s.resources_done" % card_id)
        if "summary" in card:
            _typed(card["summary"], str, "%s.summary" % card_id)
        for field in ("started_at", "updated_at", "done_at", "content_verified_at_completion"):
            if field in card and card[field] is not None:
                _typed(card[field], str, "%s.%s" % (card_id, field))
        if "solution_viewed" in card and type(card["solution_viewed"]) is not bool:
            raise MigrationError("学习记录的 solution_viewed 格式不正确。")
        ui = card.get("ui", {})
        if "phase" in ui and ui["phase"] not in ("learn", "practice", "wrap"):
            raise MigrationError("学习记录的阶段格式不正确。")
        if "anchor" in ui and not isinstance(ui["anchor"], str):
            raise MigrationError("学习记录的阅读位置格式不正确。")
        if "learn_completed" in ui and type(ui["learn_completed"]) is not bool:
            raise MigrationError("学习记录的 learn_completed 格式不正确。")
        if version == 2 and "learn_completed" not in ui:
            raise MigrationError("版本 2 的卡片记录缺少 learn_completed。")
        for question_id, question in card.get("questions", {}).items():
            _typed(question_id, str, "questions 中的题目 id")
            _typed(question, dict, "questions 中的记录")
            if "my_answer" in question:
                _typed(question["my_answer"], str, "my_answer")
            if "self_mark" in question and question["self_mark"] not in (None, "correct", "wrong"):
                raise MigrationError("学习记录的自测标记格式不正确。")
        for check_id, check in card.get("checks", {}).items():
            _typed(check_id, str, "checks 中的题目 id")
            _typed(check, dict, "checks 中的记录")
            _typed(check.get("attempts"), list, "attempts")
            for attempt in check["attempts"]:
                _typed(attempt, dict, "attempts 中的记录")
                _typed(attempt.get("value"), str, "attempts.value")
                if "correct" in attempt and type(attempt["correct"]) is not bool:
                    raise MigrationError("学习记录的判题结果格式不正确。")
        writeup = card.get("writeup", {})
        if "text" in writeup:
            _typed(writeup["text"], str, "writeup.text")
        if "rubric_checked" in writeup:
            _typed(writeup["rubric_checked"], list, "writeup.rubric_checked")
    for event in progress["events"]:
        _typed(event, dict, "events 中的记录")
    return progress


def migrate_v1_to_v2(progress):
    """Return a new object; never mutate the original or discard unknown fields."""
    validate_progress(progress)
    if progress["spec_version"] != 1:
        raise MigrationError("v1 → v2 迁移只能用于版本 1。")
    result = deepcopy(progress)
    result.setdefault("pending_new_cards", [])
    for card in result["cards"].values():
        ui = card.setdefault("ui", {})
        ui.setdefault("learn_completed", card.get("status") == "done" or bool(card.get("questions")) or bool(card.get("checks")))
    result["spec_version"] = 2
    validate_progress(result)
    return result


MIGRATIONS = {1: migrate_v1_to_v2}


def migrate(progress):
    """Run each registered migration, validating each complete result."""
    validate_progress(progress)
    result = deepcopy(progress)
    while result["spec_version"] < CURRENT_VERSION:
        version = result["spec_version"]
        migration = MIGRATIONS.get(version)
        if migration is None:
            raise MigrationError("没有找到学习记录版本 %s 的升级方法。" % version)
        result = migration(result)
        validate_progress(result)
        if result["spec_version"] != version + 1:
            raise MigrationError("学习记录升级没有按顺序完成。")
    return result
