import unittest

from grading import answers_match, parse_number


class ShortAnswerEquivalence(unittest.TestCase):
    def test_fraction_decimal_forms_all_match(self):
        for typed in ["3/4", "0.75", ".75", " 0.750 ", "6/8", "3 / 4", "0.7500"]:
            with self.subTest(typed=typed):
                self.assertTrue(answers_match(typed, "3/4"))
                self.assertTrue(answers_match(typed, "0.75"))

    def test_wrong_numbers_do_not_match(self):
        for typed in ["0.7", "4/3", "-3/4", "0.76", "", "abc"]:
            with self.subTest(typed=typed):
                self.assertFalse(answers_match(typed, "3/4"))

    def test_integers_negatives_and_mixed_numbers(self):
        self.assertTrue(answers_match("-12", "-12"))
        self.assertTrue(answers_match("−12", "-12"))          # unicode minus sign
        self.assertTrue(answers_match("12.0", "12"))
        self.assertTrue(answers_match("1,200", "1200"))
        self.assertTrue(answers_match("1 1/2", "3/2"))
        self.assertTrue(answers_match("-1 1/2", "-1.5"))
        self.assertFalse(answers_match("12", "-12"))

    def test_percent_sign_is_optional(self):
        self.assertTrue(answers_match("25%", "25"))
        self.assertTrue(answers_match("25", "25%"))
        self.assertFalse(answers_match("0.25", "25"))

    def test_word_answers_ignore_case_and_spaces(self):
        self.assertTrue(answers_match("  Right   Triangle ", "right triangle"))
        self.assertTrue(answers_match("PRIME", "prime"))
        self.assertFalse(answers_match("righttriangle", "right triangle"))

    def test_division_by_zero_is_not_a_number(self):
        self.assertIsNone(parse_number("1/0"))
        self.assertFalse(answers_match("1/0", "0"))


if __name__ == "__main__":
    unittest.main()
