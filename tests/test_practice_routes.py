"""End-to-end checks through Flask's test client (no browser needed)."""
import json
import random
import re
import unittest

import app as tutor_app
from fundamentals import make_round
from grading import answers_match, parse_number
from tests.helpers import fresh_db


class FundamentalsGenerator(unittest.TestCase):
    def test_every_question_is_gradable_and_choices_are_valid(self):
        rng = random.Random(42)
        for grade in ["Kindergarten", "Grade 3", "Grade 6", "Grade 8", "Grade 11", "Adult learner", None]:
            for _ in range(100):
                problems = make_round(grade, rng=rng)
                self.assertEqual(len(problems), 10)
                self.assertEqual(sum(p["answer_type"] == "multiple_choice" for p in problems), 5)
                for p in problems:
                    self.assertTrue(answers_match(p["correct_answer"], p["correct_answer"]))
                    if p["choices"]:
                        values = [parse_number(c) for c in p["choices"]]
                        self.assertEqual(len(set(values)), 4, p)
                        self.assertEqual(sum(answers_match(c, p["correct_answer"]) for c in p["choices"]), 1, p)


class PracticeRoutes(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        tutor_app.app.config["TESTING"] = True
        self.client = tutor_app.app.test_client()

    def csrf(self):
        self.client.get("/login")
        with self.client.session_transaction() as s:
            return s["_csrf"]

    def signup_student(self, username="tester_1"):
        token = self.csrf()
        self.client.post("/signup", data={
            "_csrf": token, "role": "student", "name": "Test Kid", "email": f"{username}@example.test",
            "username": username, "grade_level": "Grade 7", "password": "testpass123", "confirm": "testpass123"})

    def test_answer_key_is_not_sent_before_submitting(self):
        self.signup_student()
        resp = self.client.post("/practice/fundamentals/start", data={"_csrf": self.csrf()})
        set_id = int(resp.headers["Location"].rsplit("/", 1)[1])
        html = self.client.get(f"/practice/set/{set_id}").get_data(as_text=True)
        first = self.db.execute("SELECT * FROM problems WHERE set_id = ? ORDER BY position", (set_id,)).fetchone()
        self.assertIn("Question 1 of 10", html)
        self.assertNotIn(first["explanation"], html)
        if first["answer_type"] == "short_answer":
            self.assertIsNone(re.search(rf'value="{re.escape(first["correct_answer"])}"', html))

    def test_full_round_and_points(self):
        self.signup_student()
        resp = self.client.post("/practice/fundamentals/start", data={"_csrf": self.csrf()})
        set_id = int(resp.headers["Location"].rsplit("/", 1)[1])
        problems = self.db.execute("SELECT * FROM problems WHERE set_id = ? ORDER BY position", (set_id,)).fetchall()
        for i, p in enumerate(problems):
            self.client.get(f"/practice/set/{set_id}")
            wrong = next((c for c in json.loads(p["choices"]) if c != p["correct_answer"]), None) \
                if p["choices"] else "999999"
            answer = p["correct_answer"] if i % 2 == 0 else wrong
            self.client.post(f"/practice/problem/{p['id']}/answer", data={"_csrf": self.csrf(), "answer": answer})
        html = self.client.get(f"/practice/set/{set_id}").get_data(as_text=True)
        self.assertIn("Round complete", html)
        self.assertIn("+50", html)                                    # 5 correct x 10 points

    def test_answer_without_csrf_token_is_rejected(self):
        self.signup_student()
        self.client.post("/practice/fundamentals/start", data={"_csrf": self.csrf()})
        pid = self.db.execute("SELECT id FROM problems LIMIT 1").fetchone()[0]
        resp = self.client.post(f"/practice/problem/{pid}/answer", data={"answer": "1"})
        self.assertEqual(resp.status_code, 400)

    def test_students_cannot_open_each_others_sets(self):
        self.signup_student("first_kid")
        self.client.post("/practice/fundamentals/start", data={"_csrf": self.csrf()})
        set_id = self.db.execute("SELECT id FROM problem_sets").fetchone()[0]
        self.client.post("/logout", data={"_csrf": self.csrf()})
        self.signup_student("second_kid")
        self.assertEqual(self.client.get(f"/practice/set/{set_id}").status_code, 404)

    def test_leaderboard_page_never_shows_real_names_or_emails(self):
        self.signup_student("board_kid")
        set_resp = self.client.post("/practice/fundamentals/start", data={"_csrf": self.csrf()})
        set_id = int(set_resp.headers["Location"].rsplit("/", 1)[1])
        p = self.db.execute("SELECT * FROM problems WHERE set_id = ? ORDER BY position", (set_id,)).fetchone()
        self.client.get(f"/practice/set/{set_id}")
        self.client.post(f"/practice/problem/{p['id']}/answer", data={"_csrf": self.csrf(), "answer": p["correct_answer"]})
        html = self.client.get("/leaderboard").get_data(as_text=True)
        self.assertIn("@board_kid", html)
        board = html.split('class="board"', 1)[1]
        self.assertNotIn("Test Kid", board)
        self.assertNotIn("example.test", board)
        self.assertNotIn("<img", board)


if __name__ == "__main__":
    unittest.main()
