"""Phase A: instant feedback, combos, stars and the header points pill."""
import json
import unittest

import app as tutor_app
from tests.helpers import fresh_db, make_user


class Helpers(unittest.TestCase):
    def test_stars(self):
        cases = {(10, 10): 3, (9, 10): 3, (8, 10): 2, (7, 10): 2, (6, 10): 1, (4, 10): 1, (3, 10): 0, (0, 10): 0, (5, 5): 3}
        for (correct, total), stars in cases.items():
            with self.subTest(correct=correct, total=total):
                self.assertEqual(tutor_app.stars_for(correct, total), stars)

    def test_combo_counts_correct_answers_in_a_row(self):
        rows = [{"id": i, "is_correct": v} for i, v in enumerate([1, 1, 0, 1, 1, 1, None, None], start=1)]
        self.assertEqual([tutor_app.combo_at(rows, i) for i in range(1, 7)], [1, 2, 0, 1, 2, 3])


class InstantAnswers(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        tutor_app.app.config["TESTING"] = True
        self.client = tutor_app.app.test_client()
        self.kid = make_user(self.db, "Kid", username="kid")
        with self.client.session_transaction() as s:
            s["user_id"] = self.kid
        resp = self.client.post("/practice/fundamentals/start", data={"_csrf": self.token()})
        self.set_id = int(resp.headers["Location"].rsplit("/", 1)[1])
        self.problems = self.db.execute("SELECT * FROM problems WHERE set_id = ? ORDER BY position",
                                        (self.set_id,)).fetchall()

    def token(self):
        self.client.get("/practice")
        with self.client.session_transaction() as s:
            return s["_csrf"]

    def answer(self, problem, correct=True, instant=True):
        if correct:
            given = problem["correct_answer"]
        elif problem["choices"]:
            given = next(c for c in json.loads(problem["choices"]) if c != problem["correct_answer"])
        else:
            given = "999999"
        self.client.get(f"/practice/set/{self.set_id}")
        headers = {"X-Requested-With": "fetch"} if instant else {}
        return self.client.post(f"/practice/problem/{problem['id']}/answer",
                                data={"_csrf": self.token(), "answer": given}, headers=headers)

    def test_instant_answer_returns_feedback_panel_and_numbers(self):
        resp = self.answer(self.problems[0])
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["correct"])
        self.assertEqual((data["points"], data["combo"], data["wallet"]), (10, 1, 10))
        self.assertIn('class="panel question-panel feedback is-right"', data["html"])
        self.assertIn("Next question", data["html"])
        self.assertIn("minutes", data["today_html"])
        self.assertTrue(data["feedback_url"].endswith(f"answered={self.problems[0]['id']}"))

    def test_combo_shows_after_two_in_a_row_and_resets_on_a_miss(self):
        self.answer(self.problems[0])
        second = self.answer(self.problems[1]).get_json()
        self.assertEqual(second["combo"], 2)
        self.assertIn("2 in a row!", second["html"])
        miss = self.answer(self.problems[2], correct=False).get_json()
        self.assertFalse(miss["correct"])
        self.assertEqual(miss["combo"], 0)
        self.assertNotIn("in a row", miss["html"])
        self.assertIn("The answer is", miss["html"])
        self.assertNotIn("✅", miss["html"])                       # a miss is never dressed up as a win

    def test_instant_errors_come_back_as_json(self):
        self.answer(self.problems[0])
        again = self.answer(self.problems[0])                       # already answered
        self.assertEqual(again.status_code, 400)
        self.assertIn("already answered", again.get_json()["error"])

    def test_classic_form_post_still_works_without_javascript(self):
        resp = self.answer(self.problems[0], instant=False)
        self.assertEqual(resp.status_code, 302)
        self.assertIn(f"answered={self.problems[0]['id']}", resp.headers["Location"])

    def test_header_shows_points_pill_for_students_only(self):
        self.answer(self.problems[0])
        html = self.client.get("/home").get_data(as_text=True)
        self.assertIn('data-wallet-value>10<', html)
        self.assertIn("data-sound-toggle", html)
        with self.client.session_transaction() as s:
            s["user_id"] = make_user(self.db, "Tutor", role="tutor")
        self.assertNotIn("data-wallet-value", self.client.get("/home").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
