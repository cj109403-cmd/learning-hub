"""Contract tests for each CONTENT_SPEC 3.6 judge, including exact boundaries."""

import unittest

from app.server.judge import judge, normalize


class JudgeTests(unittest.TestCase):
    def assertCases(self, check, accepted, rejected):
        for value in accepted:
            with self.subTest(value=value, expected=True):
                self.assertIs(judge(check, value), True)
        for value in rejected:
            with self.subTest(value=value, expected=False):
                self.assertIs(judge(check, value), False)

    def test_integer_accepts_whole_numbers_full_width_and_optional_percent(self):
        self.assertCases({"kind": "integer", "expected": 7},
                         ["7", " 7 ", "+7", "007", "７", "＋７", "７％", " 7 % "],
                         ["", " ", "7.0", "7e0", "8", "7%%", "7％%", "7x", "7 0", "0x7", "七", "NaN"])

    def test_integer_negative_and_zero(self):
        self.assertCases({"kind": "integer", "expected": -3}, ["-3", "－３％"], ["3", "-3.0"])
        self.assertCases({"kind": "integer", "expected": 0}, ["0", "-0", "+０%"], ["1"])

    def test_integer_very_long_input_is_a_wrong_answer_not_an_exception(self):
        self.assertIs(judge({"kind": "integer", "expected": 7}, "7" * 5000), False)

    def test_number_uses_inclusive_exact_decimal_tolerance(self):
        self.assertCases({"kind": "number", "expected": 52.4, "tolerance": 0.05},
                         ["52.4", "52.35", "52.45", "５２．４％", "+52.4%", "5.24e1"],
                         ["52.349999", "52.450001", "", "bad", "52.4%%", "nan", "NaN", "Infinity", "-inf"])

    def test_number_precision_does_not_round_an_outside_value_onto_boundary(self):
        self.assertCases({"kind": "number", "expected": 1, "tolerance": 0.1},
                         ["0.9", "1.1", "1.099999999999999999999999999999"],
                         ["1.10000000000000000000000000001", "0.89999999999999999999999999999"])

    def test_number_zero_tolerance_and_negative_expected(self):
        self.assertCases({"kind": "number", "expected": -0.25, "tolerance": 0},
                         ["-0.25", "－０．２５０％"], ["-0.25001", "0.25"])

    def test_text_normalize_options_individually_and_in_order(self):
        self.assertEqual(normalize("  a A\n", ["trim"]), "a A")
        self.assertEqual(normalize("AbC", ["upper"]), "ABC")
        self.assertEqual(normalize("AbC", ["lower"]), "abc")
        self.assertEqual(normalize(" A\tT\n G\u3000C ", ["no_space"]), "ATGC")
        self.assertEqual(normalize(" A ", ["trim", "upper", "lower"]), "a")
        self.assertCases({"kind": "text", "expected": "ATGC", "normalize": ["trim", "upper", "no_space"]},
                         ["ATGC", " at g\nc ", "a\ttGc"], ["ATGA", "GCTA", "", "ATＧC"])

    def test_text_without_normalize_is_exact_and_case_sensitive(self):
        self.assertCases({"kind": "text", "expected": "Hello"}, ["Hello"], ["hello", " Hello", "Hello\n"])

    def test_choice_compares_id_not_label(self):
        self.assertCases({"kind": "choice", "expected": "b", "options": [{"id": "b", "text": "仅合格项"}]},
                         ["b"], ["a", "B", " b ", "仅合格项", ""])

    def test_set_handles_all_separators_order_duplicates_and_normalize(self):
        check = {"kind": "set", "expected": ["P02", "P07", "P11"], "normalize": ["trim", "upper"]}
        self.assertCases(check,
                         ["P02 P07 P11", "p11，P02;p07", "P07；\nP11, P02\tP02", " P02,，;； P11 P07 "],
                         ["P02 P07", "P02 P07 P11 P12", "P02 P07 P11x", "", "P02/P07/P11"])

    def test_set_without_upper_does_not_change_case(self):
        self.assertCases({"kind": "set", "expected": ["a", "b"], "normalize": ["trim"]},
                         ["b,a,a"], ["A,B", "a"])

    def test_non_text_values_and_unknown_kinds_are_rejected(self):
        for kind in ("integer", "number", "text", "choice", "set"):
            check = {"kind": kind, "expected": 1, "tolerance": 0.1}
            for value in (None, 1, 1.0, True, [], {}):
                with self.subTest(kind=kind, value=value):
                    self.assertIs(judge(check, value), False)
        self.assertIs(judge({"kind": "unknown", "expected": "x"}, "x"), False)


if __name__ == "__main__":
    unittest.main()
