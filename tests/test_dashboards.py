import unittest
from datetime import timedelta

import app as tutor_app
import practice as pr
import streaks
from tests.helpers import answer, fresh_db, make_user, noon


def answers(db, user, topic, right, wrong, when=None):
    when = when or noon(3)
    for i in range(right + wrong):
        answer(db, user, when + timedelta(minutes=i), 30, correct=i < right, topic=topic)


class TopicStats(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.user = make_user(self.db, username="kid")

    def test_weakest_two_topics_are_marked(self):
        answers(self.db, self.user, "Fractions", right=1, wrong=3)     # 25%
        answers(self.db, self.user, "Decimals", right=2, wrong=2)      # 50%
        answers(self.db, self.user, "Percents", right=3, wrong=1)      # 75%
        answers(self.db, self.user, "Exponents", right=4, wrong=0)     # 100%
        stats = pr.topic_accuracy(self.db, self.user)
        self.assertEqual([(s["topic"], s["accuracy"]) for s in stats],
                         [("Fractions", 25), ("Decimals", 50), ("Percents", 75), ("Exponents", 100)])
        self.assertEqual([s["topic"] for s in stats if s["weak"]], ["Fractions", "Decimals"])

    def test_too_few_answers_or_perfect_scores_are_never_weak(self):
        answers(self.db, self.user, "Fractions", right=0, wrong=2)     # 0%, but only 2 answers
        answers(self.db, self.user, "Exponents", right=5, wrong=0)     # perfect
        answers(self.db, self.user, "Decimals", right=2, wrong=1)      # 67%, 3 answers
        self.assertEqual([s["topic"] for s in pr.topic_accuracy(self.db, self.user) if s["weak"]], ["Decimals"])

    def test_counts_every_practice_type(self):
        answer(self.db, self.user, noon(), 30, source="topic", topic="Ratios")
        answer(self.db, self.user, noon(), 30, source="photo", topic="Ratios", correct=False)
        self.assertEqual(pr.topic_accuracy(self.db, self.user)[0]["answered"], 2)


class RecentAndSummary(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)

    def test_recent_attempts_are_mine_newest_first_and_limited(self):
        me, other = make_user(self.db, "Me"), make_user(self.db, "Other")
        for i in range(12):
            answer(self.db, me, noon() + timedelta(minutes=i), 30, question=f"Mine {i}")
        answer(self.db, other, noon(), 30, question="Not mine")
        recent = pr.recent_attempts(self.db, me)
        self.assertEqual(len(recent), 10)
        self.assertEqual(recent[0]["question_text"], "Mine 11")
        self.assertNotIn("Not mine", [r["question_text"] for r in recent])

    def test_summary_for_tutor_table(self):
        kid = make_user(self.db, "Kid")
        answers(self.db, kid, "Fractions", right=1, wrong=2, when=noon(-3))   # last week
        answers(self.db, kid, "Decimals", right=3, wrong=1, when=noon(3))     # this week
        summary = pr.student_summary(self.db, kid, noon(3) + timedelta(hours=1))
        self.assertEqual(summary["answered"], 7)
        self.assertEqual(summary["week_answered"], 4)
        self.assertEqual(summary["accuracy"], 57)                             # 4 of 7
        self.assertEqual(summary["weakest_topic"], "Fractions")
        self.assertIsNotNone(summary["last_active"])

    def test_summary_with_no_practice(self):
        kid = make_user(self.db, "New Kid")
        summary = pr.student_summary(self.db, kid, noon())
        self.assertEqual((summary["answered"], summary["accuracy"], summary["last_active"]), (0, None, None))

    def test_weekly_rank(self):
        a, b = make_user(self.db, "A", username="a"), make_user(self.db, "B", username="b")
        answers(self.db, a, "Fractions", right=3, wrong=0)                    # 30 points
        answers(self.db, b, "Fractions", right=1, wrong=0)                    # 10 points
        week = streaks.week_start_utc(noon(3))
        self.assertEqual((pr.user_rank(self.db, week, a), pr.user_rank(self.db, week, b)), (1, 2))
        self.assertIsNone(pr.user_rank(self.db, week, make_user(self.db, "C", username="c")))


class DashboardAccess(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        tutor_app.app.config["TESTING"] = True
        self.client = tutor_app.app.test_client()
        self.tutor = make_user(self.db, "Tina Tutor", role="tutor")
        self.other_tutor = make_user(self.db, "Oscar Tutor", role="tutor")
        self.mine = make_user(self.db, "Mia Mine", username="mia")
        self.theirs = make_user(self.db, "Theo Theirs", username="theo")
        self.db.execute("UPDATE users SET tutor_id = ? WHERE id = ?", (self.tutor, self.mine))
        self.db.execute("UPDATE users SET tutor_id = ? WHERE id = ?", (self.other_tutor, self.theirs))
        self.db.commit()
        answer(self.db, self.mine, noon(), 30, question="Mia's question")
        answer(self.db, self.theirs, noon(), 30, question="Theo's question")

    def login_as(self, user_id):
        with self.client.session_transaction() as s:
            s["user_id"] = user_id

    def test_tutor_sees_only_linked_students(self):
        self.login_as(self.tutor)
        dash = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Mia Mine", dash)
        self.assertNotIn("Theo Theirs", dash)
        detail = self.client.get(f"/dashboard/students/{self.mine}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Mia&#39;s question", detail.get_data(as_text=True))
        self.assertEqual(self.client.get(f"/dashboard/students/{self.theirs}").status_code, 404)

    def test_students_cannot_open_student_pages(self):
        self.login_as(self.mine)
        self.assertEqual(self.client.get(f"/dashboard/students/{self.mine}").status_code, 404)
        self.assertEqual(self.client.get(f"/dashboard/students/{self.theirs}").status_code, 404)

    def test_student_dashboard_shows_only_own_answers(self):
        self.login_as(self.mine)
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("Mia&#39;s question", html)
        self.assertNotIn("Theo&#39;s question", html)

    def test_practice_this_starts_a_single_topic_round(self):
        self.login_as(self.mine)
        self.client.get("/dashboard")
        with self.client.session_transaction() as s:
            token = s["_csrf"]
        resp = self.client.post("/practice/fundamentals/start", data={"_csrf": token, "topic": "Fractions"})
        set_id = int(resp.headers["Location"].rsplit("/", 1)[1])
        topics = {r[0] for r in self.db.execute("SELECT topic FROM problems WHERE set_id = ?", (set_id,))}
        self.assertEqual(topics, {"Fractions"})


if __name__ == "__main__":
    unittest.main()
