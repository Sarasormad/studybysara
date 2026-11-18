"""Tutor views: class overview rules, Home/Dashboard/student page content, nav and profile editing."""
import unittest
from datetime import timedelta

import app as tutor_app
import practice as pr
from tests.helpers import answer, fresh_db, make_user, noon, practice_minutes


def link(db, tutor, *students):
    for s in students:
        db.execute("UPDATE users SET tutor_id = ? WHERE id = ?", (tutor, s))
    db.commit()


def reasons_for(overview, user_id):
    return next(r["reasons"] for r in overview["rows"] if r["student"]["id"] == user_id)


class ClassOverview(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.tutor = make_user(self.db, "Tina", role="tutor")

    def test_empty_class(self):
        o = pr.class_overview(self.db, self.tutor, noon())
        self.assertEqual((o["count"], o["active_week"], o["accuracy"], o["weak_topics"], o["attention"]),
                         (0, 0, None, [], []))

    def test_never_practiced_and_inactive(self):
        new, idle, busy = (make_user(self.db, n) for n in ("New", "Idle", "Busy"))
        link(self.db, self.tutor, new, idle, busy)
        answer(self.db, idle, noon(0), 30)
        answer(self.db, busy, noon(4), 30)
        o = pr.class_overview(self.db, self.tutor, noon(4))
        self.assertEqual(reasons_for(o, new), ["Hasn't practiced yet"])
        self.assertEqual(reasons_for(o, idle), ["Inactive for 4 days"])
        self.assertEqual(reasons_for(o, busy), [])
        self.assertEqual({r["student"]["id"] for r in o["attention"]}, {new, idle})

    def test_low_accuracy_needs_enough_answers(self):
        few, many = make_user(self.db, "Few"), make_user(self.db, "Many")
        link(self.db, self.tutor, few, many)
        for i in range(9):
            answer(self.db, few, noon() + timedelta(minutes=i), 30, correct=False)
        for i in range(10):
            answer(self.db, many, noon() + timedelta(minutes=i), 30, correct=i < 4)
        o = pr.class_overview(self.db, self.tutor, noon())
        self.assertEqual(reasons_for(o, few), [])
        self.assertEqual(reasons_for(o, many), ["Accuracy is 40%"])
        self.assertEqual(o["accuracy"], round(100 * 4 / 19))

    def test_lost_streak_is_flagged_but_a_running_one_is_not(self):
        lost, running = make_user(self.db, "Lost"), make_user(self.db, "Running")
        link(self.db, self.tutor, lost, running)
        for d in range(3):
            practice_minutes(self.db, lost, noon(d), 10)
        for d in range(5):
            practice_minutes(self.db, running, noon(d), 10)
        o = pr.class_overview(self.db, self.tutor, noon(5))
        self.assertIn("Lost a 3-day streak", reasons_for(o, lost))
        self.assertEqual(reasons_for(o, running), [])

    def test_weak_topics_and_only_my_students(self):
        kid, stranger = make_user(self.db, "Kid"), make_user(self.db, "Stranger")
        link(self.db, self.tutor, kid)
        for i in range(5):
            answer(self.db, kid, noon() + timedelta(minutes=i), 30, topic="Fractions", correct=i < 2)
            answer(self.db, kid, noon() + timedelta(minutes=i), 30, topic="Exponents")          # perfect
            answer(self.db, stranger, noon() + timedelta(minutes=i), 30, topic="Decimals", correct=False)
        o = pr.class_overview(self.db, self.tutor, noon())
        self.assertEqual(o["count"], 1)
        self.assertEqual([(t["topic"], t["accuracy"], t["students"]) for t in o["weak_topics"]],
                         [("Fractions", 40, 1)])


class TutorPages(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        tutor_app.app.config["TESTING"] = True
        self.client = tutor_app.app.test_client()
        self.tutor = make_user(self.db, "Tina Tutor", role="tutor")
        self.db.execute("UPDATE users SET subject = 'Algebra' WHERE id = ?", (self.tutor,))
        self.kid = make_user(self.db, "Kai Kid", username="kai")
        self.new = make_user(self.db, "Nia New", username="nia")
        link(self.db, self.tutor, self.kid, self.new)
        for i in range(4):
            answer(self.db, self.kid, noon() + timedelta(minutes=i), 30, topic="Fractions", correct=i < 2)
        with self.client.session_transaction() as s:
            s["user_id"] = self.tutor

    def token(self):
        with self.client.session_transaction() as s:
            s.setdefault("_csrf", "test-token")
            return s["_csrf"]

    def page(self, url):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_home_shows_class_summary_and_attention(self):
        html = self.page("/home")
        self.assertIn("class-summary", html)
        self.assertIn("Needs attention", html)
        self.assertIn("Hasn&#39;t practiced yet", html)
        self.assertNotIn("Create and assign problems", html)

    def test_dashboard_has_sortable_table_with_card_labels(self):
        html = self.page("/dashboard")
        self.assertIn("data-sortable", html)
        self.assertIn('class="sort-btn"', html)
        self.assertIn('data-label="', html)
        self.assertIn("needs-attention", html)

    def test_student_page_has_chart_recommendations_and_level(self):
        html = self.page(f"/dashboard/students/{self.kid}")
        self.assertIn("bar-chart", html)
        self.assertIn("Practice next", html)
        self.assertIn("Fractions", html)
        self.assertIn("streak shield", html)
        self.assertIn("level-card", html)

    def test_tutor_nav_has_no_practice_shop_or_sound(self):
        html = self.page("/profile")
        self.assertNotIn('href="/practice"', html)
        self.assertNotIn('href="/shop"', html)
        self.assertNotIn("data-sound-toggle", html)

    def test_tutor_can_edit_name_and_subjects(self):
        self.assertIn('name="subject"', self.page("/profile/edit"))
        resp = self.client.post("/profile/edit", data={"_csrf": self.token(), "name": "Tina T.",
                                                       "subject": "Algebra, Geometry"})
        self.assertEqual(resp.status_code, 302)
        row = self.db.execute("SELECT name, subject FROM users WHERE id = ?", (self.tutor,)).fetchone()
        self.assertEqual(tuple(row), ("Tina T.", "Algebra, Geometry"))

    def test_tutor_edit_rejects_blank_fields(self):
        html = self.client.post("/profile/edit", data={"_csrf": self.token(), "name": " ", "subject": ""},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("Name is required.", html)
        self.assertEqual(self.db.execute("SELECT name FROM users WHERE id = ?", (self.tutor,)).fetchone()[0],
                         "Tina Tutor")


if __name__ == "__main__":
    unittest.main()
