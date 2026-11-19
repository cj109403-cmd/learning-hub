import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from app.server.migrations import (MigrationError, UnsupportedVersion, migrate,
                                   migrate_v1_to_v2, new_progress, validate_progress)


FIXTURE = Path(__file__).parent / "fixtures" / "progress_versions" / "v1.json"


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.original = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_real_v1_rules_and_unknown_fields_are_preserved(self):
        before = copy.deepcopy(self.original)
        upgraded = migrate(self.original)
        self.assertEqual(self.original, before)
        self.assertEqual(upgraded["spec_version"], 2)
        self.assertEqual(upgraded["pending_new_cards"], [])
        for card in ("finished", "answered", "checked", "retired-and-unknown"):
            self.assertTrue(upgraded["cards"][card]["ui"]["learn_completed"])
        # Freely switching to practice does not imply reading was completed.
        self.assertFalse(upgraded["cards"]["reading"]["ui"]["learn_completed"])
        restored = copy.deepcopy(upgraded)
        restored["spec_version"] = 1
        del restored["pending_new_cards"]
        for card_id, card in restored["cards"].items():
            del card["ui"]["learn_completed"]
            if "ui" not in before["cards"][card_id]:
                del card["ui"]
        self.assertEqual(restored, before)

    def test_migration_is_idempotent_and_does_not_alias(self):
        current = migrate(self.original)
        again = migrate(current)
        self.assertEqual(current, again)
        again["cards"]["answered"]["questions"]["q1"]["my_answer"] = "改动"
        self.assertNotEqual(again, current)

    def test_existing_forward_fields_are_not_reset(self):
        self.original["pending_new_cards"] = ["pending"]
        self.original["cards"]["reading"]["ui"]["learn_completed"] = True
        upgraded = migrate(self.original)
        self.assertEqual(upgraded["pending_new_cards"], ["pending"])
        self.assertTrue(upgraded["cards"]["reading"]["ui"]["learn_completed"])

    def test_new_progress_does_not_preseed_first_visit(self):
        fresh = new_progress()
        self.assertNotIn("known_cards", fresh)
        self.assertNotIn("welcome_seen_at", fresh)
        validate_progress(fresh)
        fresh["cards"]["x"] = {}
        self.assertEqual(new_progress()["cards"], {})

    def test_future_version_rejected_before_interpreting_new_schema(self):
        with self.assertRaises(UnsupportedVersion):
            migrate({"spec_version": 3, "cards": "a future schema"})

    def test_invalid_known_fields_are_rejected(self):
        for mutate in (lambda p: p.update(spec_version=True),
                       lambda p: p.update(cards=[]),
                       lambda p: p.update(events={}),
                       lambda p: p.update(known_cards=[5]),
                       lambda p: p["cards"]["reading"].update(ui=None),
                       lambda p: p["cards"]["reading"]["ui"].update(learn_completed="true"),
                       lambda p: p["cards"]["checked"]["checks"]["c1"].update(attempts="bad")):
            with self.subTest(mutate=mutate):
                record = copy.deepcopy(self.original)
                mutate(record)
                with self.assertRaises(MigrationError):
                    migrate(record)

    def test_failed_migration_never_changes_input(self):
        before = copy.deepcopy(self.original)
        def failure(record):
            record["cards"].clear()
            raise MigrationError("模拟升级中断")
        with patch("app.server.migrations.MIGRATIONS", {1: failure}):
            with self.assertRaises(MigrationError):
                migrate(self.original)
        self.assertEqual(self.original, before)

    def test_summary_is_text_while_completion_time_can_be_null(self):
        for version in (1, 2):
            with self.subTest(version=version):
                value = copy.deepcopy(self.original) if version == 1 else migrate(self.original)
                value["cards"]["reading"]["done_at"] = None
                validate_progress(value)
                for invalid in (None, False, [], {}):
                    value["cards"]["reading"]["summary"] = invalid
                    with self.assertRaises(MigrationError):
                        validate_progress(value)

    def test_partial_check_records_are_corrupt_instead_of_crashing_consumers(self):
        for check in ({}, {"attempts": None}, {"attempts": [{}]},
                      {"attempts": [{"correct": False}]},
                      {"attempts": [{"value": None, "correct": False}]},
                      {"attempts": [{"value": [], "correct": True}]}):
            with self.subTest(check=check):
                value = migrate(self.original)
                value["cards"]["checked"]["checks"]["c1"] = check
                with self.assertRaises(MigrationError):
                    validate_progress(value)

    def test_wrong_migration_target_is_rejected(self):
        def unchanged(record):
            return record
        with patch("app.server.migrations.MIGRATIONS", {1: unchanged}):
            with self.assertRaises(MigrationError):
                migrate(self.original)
        with self.assertRaises(MigrationError):
            migrate_v1_to_v2(new_progress())


if __name__ == "__main__":
    unittest.main()
