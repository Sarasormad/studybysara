import unittest

import app as tutor_app
from fundamentals import TIER_TOPICS, TOPICS, tier_for_grade
from tests.helpers import fresh_db, make_user


class LeaderboardAndEarnPoints(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        tutor_app.app.config["TESTING"] = True
        self.client = tutor_app.app.test_client()
        self.kid = make_user(self.db, "Kid", username="kid", grade="Grade 7")
        self.login(self.kid)

    def login(self, user_id):
        with self.client.session_transaction() as s:
            s["user_id"] = user_id

    def token(self):
        self.client.get("/leaderboard")
        with self.client.session_transaction() as s:
            return s["_csrf"]

    def test_leaderboard_is_all_time_only(self):
        html = self.client.get("/leaderboard").get_data(as_text=True)
        self.assertNotIn("This Week", html)
        self.assertIn("All-time points", html)

    def test_students_see_earn_button_tutors_do_not(self):
        html = self.client.get("/leaderboard").get_data(as_text=True)
        self.assertIn("Earn more points!", html)
        self.assertIn('href="/leaderboard/earn"', html)
        self.login(make_user(self.db, "Tutor", role="tutor"))
        self.assertNotIn("Earn more points!", self.client.get("/leaderboard").get_data(as_text=True))
        self.assertEqual(self.client.get("/leaderboard/earn").status_code, 302)   # tutors are sent away

    def test_picker_lists_the_fundamentals_topics_for_the_grade(self):
        html = self.client.get("/leaderboard/earn").get_data(as_text=True)
        self.assertIn("Mix of everything", html)
        for key in TIER_TOPICS[tier_for_grade("Grade 7")]:
            self.assertIn(TOPICS[key], html)
        self.assertIn('name="back" value="leaderboard"', html)

    def test_picking_a_topic_starts_a_10_question_round_that_returns_to_the_leaderboard(self):
        resp = self.client.post("/practice/fundamentals/start",
                                data={"_csrf": self.token(), "topic": "Percents", "back": "leaderboard"})
        set_id = int(resp.headers["Location"].rsplit("/", 1)[1])
        rows = self.db.execute("SELECT topic, source FROM problems WHERE set_id = ?", (set_id,)).fetchall()
        self.assertEqual(len(rows), 10)
        self.assertEqual({(r["topic"], r["source"]) for r in rows}, {("Percents", "fundamentals")})
        html = self.client.get(f"/practice/set/{set_id}").get_data(as_text=True)
        self.assertIn('href="/leaderboard">← Leaderboard', html)

    def test_mix_of_everything_starts_a_mixed_round(self):
        resp = self.client.post("/practice/fundamentals/start",
                                data={"_csrf": self.token(), "topic": "", "back": "leaderboard"})
        set_id = int(resp.headers["Location"].rsplit("/", 1)[1])
        topics = {r[0] for r in self.db.execute("SELECT topic FROM problems WHERE set_id = ?", (set_id,))}
        self.assertGreater(len(topics), 1)

    def test_rounds_from_the_practice_page_still_go_back_to_practice(self):
        resp = self.client.post("/practice/fundamentals/start", data={"_csrf": self.token()})
        html = self.client.get(resp.headers["Location"]).get_data(as_text=True)
        self.assertIn('href="/practice">← Practice', html)


if __name__ == "__main__":
    unittest.main()
