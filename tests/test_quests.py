"""Phase B: levels, daily quests, the reward chest, and the points-table upgrade."""
import os
import sqlite3
import tempfile
import unittest
from datetime import timedelta

import app as tutor_app
import practice as pr
import quests as qs
import streaks
from config import CHEST_POINTS
from tests.helpers import answer, fresh_db, make_user, noon, practice_minutes


def set_quests(db, user_id, when, quests):
    """Replace a student's quests for that day (so tests control exactly which quests exist)."""
    day = streaks.practice_day(when).isoformat()
    db.execute("DELETE FROM daily_quests WHERE user_id = ? AND day = ?", (user_id, day))
    for slot, (kind, target, topic) in enumerate(quests, start=1):
        db.execute("INSERT INTO daily_quests (user_id, day, slot, kind, target, topic, title, created_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (user_id, day, slot, kind, target, topic, f"{kind} {target}", pr.to_db(when)))
    db.commit()


class Levels(unittest.TestCase):
    def test_curve(self):
        self.assertEqual([qs.level_floor(n) for n in range(1, 6)], [0, 100, 300, 600, 1000])
        self.assertEqual([qs.level_info(p)["level"] for p in (0, 99, 100, 299, 300, 999, 1000)], [1, 1, 2, 2, 3, 4, 5])
        info = qs.level_info(352)
        self.assertEqual((info["level"], info["into"], info["span"], info["to_go"], info["percent"]), (3, 52, 300, 248, 17))


class PickingQuests(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.kid = make_user(self.db, "Kid", username="kid")

    def test_three_quests_stored_once_per_day(self):
        first = qs.todays_quests(self.db, self.kid, noon(0))
        again = qs.todays_quests(self.db, self.kid, noon(0) + timedelta(hours=3))
        self.assertEqual(len(first), 3)
        self.assertEqual([q["title"] for q in first], [q["title"] for q in again])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM daily_quests").fetchone()[0], 3)
        qs.todays_quests(self.db, self.kid, noon(1))                     # a new day, new quests
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM daily_quests").fetchone()[0], 6)

    def test_slots_are_easy_skill_focus(self):
        kinds = [q["kind"] for q in qs.todays_quests(self.db, self.kid, noon(0))]
        self.assertIn(kinds[0], ("answer", "minutes"))
        self.assertIn(kinds[1], ("correct", "combo", "perfect"))
        self.assertEqual(kinds[2], "round")                              # no weak topic yet

    def test_focus_quest_targets_the_weakest_fundamentals_topic(self):
        for i in range(4):
            answer(self.db, self.kid, noon(-1) + timedelta(minutes=i), 30, correct=i == 0, topic="Fractions")
        focus = qs.todays_quests(self.db, self.kid, noon(0))[2]
        self.assertEqual((focus["kind"], focus["topic"]), ("topic", "Fractions"))

    def test_struggling_students_get_easier_skill_quests(self):
        for i in range(10):                                              # 30% accuracy
            answer(self.db, self.kid, noon(-1) + timedelta(minutes=i), 30, correct=i < 3)
        skill = qs.todays_quests(self.db, self.kid, noon(0))[1]
        self.assertIn((skill["kind"], skill["target"]), [("correct", 6), ("combo", 3)])


class Progress(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.kid = make_user(self.db, "Kid", username="kid")

    def progress(self, when=None):
        return {q["kind"]: q["progress"] for q in qs.todays_quests(self.db, self.kid, when or noon(0))}

    def test_answer_correct_and_combo(self):
        set_quests(self.db, self.kid, noon(0), [("answer", 15, None), ("correct", 10, None), ("combo", 5, None)])
        for i, ok in enumerate([1, 1, 1, 0, 1, 1]):
            answer(self.db, self.kid, noon(0) + timedelta(minutes=i), 30, correct=bool(ok))
        answer(self.db, self.kid, noon(-1), 30)                         # yesterday doesn't count
        self.assertEqual(self.progress(), {"answer": 6, "correct": 5, "combo": 3})

    def test_topic_and_minutes(self):
        set_quests(self.db, self.kid, noon(0), [("minutes", 10, None), ("topic", 5, "Fractions"), ("answer", 15, None)])
        answer(self.db, self.kid, noon(0), 120, topic="Fractions")
        answer(self.db, self.kid, noon(0) + timedelta(minutes=5), 60, topic="Decimals")
        p = self.progress()
        self.assertEqual((p["minutes"], p["topic"]), (3, 1))

    def test_rounds_and_three_star_rounds(self):
        set_quests(self.db, self.kid, noon(0), [("round", 1, None), ("perfect", 1, None), ("answer", 15, None)])
        set_id = pr.create_set(self.db, self.kid, "fundamentals", [
            {"topic": "T", "question_text": f"q{i}", "answer_type": "short_answer", "correct_answer": "4"} for i in range(10)], noon(0))
        for i, p in enumerate(pr.set_problems(self.db, self.kid, set_id)):
            pr.mark_served(self.db, p["id"], noon(0))
            pr.record_attempt(self.db, self.kid, p["id"], "4" if i < 9 else "5", noon(0) + timedelta(seconds=30 * i))
        p = self.progress()
        self.assertEqual((p["round"], p["perfect"]), (1, 1))            # 9/10 = 3 stars


class Chest(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.kid = make_user(self.db, "Kid", username="kid")
        set_quests(self.db, self.kid, noon(0), [("answer", 2, None), ("correct", 1, None), ("combo", 1, None)])

    def test_locked_until_every_quest_is_done_then_opens_once(self):
        self.assertEqual(qs.chest_state(self.db, self.kid, noon(0)), "locked")
        self.assertEqual(qs.open_chest(self.db, self.kid, noon(0)), 0)
        answer(self.db, self.kid, noon(0), 30)
        answer(self.db, self.kid, noon(0) + timedelta(minutes=1), 30)
        self.assertEqual(qs.chest_state(self.db, self.kid, noon(0)), "ready")
        self.assertEqual(qs.open_chest(self.db, self.kid, noon(0)), CHEST_POINTS)
        self.assertEqual(qs.open_chest(self.db, self.kid, noon(0)), 0)   # only once a day
        self.assertEqual(qs.chest_state(self.db, self.kid, noon(0)), "opened")
        total = self.db.execute("SELECT SUM(points) FROM points_ledger WHERE user_id = ?", (self.kid,)).fetchone()[0]
        self.assertEqual(total, 20 + CHEST_POINTS)                       # 2 correct answers + chest
        rows, _ = pr.leaderboard(self.db, None)
        self.assertEqual(rows[0]["points"], 20 + CHEST_POINTS)           # chest points count everywhere

    def test_what_changed_reports_quests_chest_and_level_up(self):
        before = qs.snapshot(self.db, self.kid, noon(0))
        for i in range(10):                                              # 100 points -> level 2
            answer(self.db, self.kid, noon(0) + timedelta(minutes=i), 30)
        events = qs.what_changed(before, qs.snapshot(self.db, self.kid, noon(0)))
        self.assertEqual(len(events["quests_done"]), 3)
        self.assertTrue(events["chest_ready"])
        self.assertEqual(events["level_up"], 2)


class LedgerUpgrade(unittest.TestCase):
    def test_old_points_table_is_rebuilt_without_losing_rows(self):
        path = os.path.join(tempfile.mkdtemp(), "old.db")
        old = sqlite3.connect(path)
        old.executescript("""
            CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, role TEXT NOT NULL, subject TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE points_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                reason TEXT NOT NULL CHECK (reason IN ('fundamentals', 'streak')), points INTEGER NOT NULL,
                attempt_id INTEGER, practice_day TEXT NOT NULL, created_at TEXT NOT NULL);
            INSERT INTO users (name, email, password_hash, role) VALUES ('A', 'a@x.test', 'x', 'student');
            INSERT INTO points_ledger (user_id, reason, points, practice_day, created_at)
                VALUES (1, 'fundamentals', 10, '2026-11-02', '2026-11-02 20:00:00'), (1, 'streak', 2, '2026-11-02', '2026-11-02 20:10:00');
        """)
        old.commit()
        old.close()
        tutor_app.DATABASE = path
        tutor_app.init_db()
        tutor_app.init_db()                                              # running again is harmless
        db = sqlite3.connect(path)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("SELECT id, reason, points FROM points_ledger ORDER BY id").fetchall(),
                         [(1, "fundamentals", 10), (2, "streak", 2)])
        db.execute("INSERT INTO points_ledger (user_id, reason, points, practice_day, created_at) "
                   "VALUES (1, 'chest', 30, '2026-11-02', '2026-11-02 21:00:00')")
        with self.assertRaises(sqlite3.IntegrityError):                  # still one chest per day
            db.execute("INSERT INTO points_ledger (user_id, reason, points, practice_day, created_at) "
                       "VALUES (1, 'chest', 30, '2026-11-02', '2026-11-02 22:00:00')")


class QuestRoutes(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        tutor_app.app.config["TESTING"] = True
        self.client = tutor_app.app.test_client()
        self.kid = make_user(self.db, "Kid", username="kid")
        with self.client.session_transaction() as s:
            s["user_id"] = self.kid

    def token(self):
        self.client.get("/practice")
        with self.client.session_transaction() as s:
            return s["_csrf"]

    def test_practice_and_home_show_quests_level_and_chest(self):
        practice = self.client.get("/practice").get_data(as_text=True)
        self.assertIn("Daily quests", practice)
        self.assertIn("Finish all 3 to open", practice)
        home = self.client.get("/home").get_data(as_text=True)
        self.assertIn("Level 1", home)
        self.assertIn("Lv 1", home)                                      # header chip

    def test_chest_route(self):
        now = pr.utcnow()
        resp = self.client.post("/quests/chest", data={"_csrf": self.token()}, follow_redirects=True)
        self.assertIn("Finish all 3", resp.get_data(as_text=True))       # not ready yet
        set_quests(self.db, self.kid, now, [("answer", 1, None), ("answer", 1, None), ("answer", 1, None)])
        answer(self.db, self.kid, now - timedelta(seconds=40), 30)
        resp = self.client.post("/quests/chest", data={"_csrf": self.token()})
        self.assertIn("chest=opened", resp.headers["Location"])
        page = self.client.get(resp.headers["Location"]).get_data(as_text=True)
        self.assertIn(f'data-countup="{CHEST_POINTS}"', page)
        self.assertIn("chest-reveal", page)

    def test_instant_answer_reports_finished_quests(self):
        now = pr.utcnow()
        set_quests(self.db, self.kid, now, [("answer", 1, None), ("answer", 5, None), ("answer", 5, None)])
        resp = self.client.post("/practice/fundamentals/start", data={"_csrf": self.token()})
        set_id = int(resp.headers["Location"].rsplit("/", 1)[1])
        p = self.db.execute("SELECT * FROM problems WHERE set_id = ? ORDER BY position", (set_id,)).fetchone()
        self.client.get(f"/practice/set/{set_id}")
        data = self.client.post(f"/practice/problem/{p['id']}/answer", headers={"X-Requested-With": "fetch"},
                                data={"_csrf": self.token(), "answer": p["correct_answer"]}).get_json()
        self.assertEqual([q["title"] for q in data["quests_done"]], ["answer 1"])
        self.assertFalse(data["chest_ready"])
        self.assertEqual(data["level"]["level"], 1)

    def test_tutors_have_no_quests_or_level(self):
        with self.client.session_transaction() as s:
            s["user_id"] = make_user(self.db, "Tutor", role="tutor")
        home = self.client.get("/home").get_data(as_text=True)
        self.assertNotIn("Daily quests", home)
        self.assertNotIn("level-chip", home)
        self.assertEqual(self.client.post("/quests/chest", data={"_csrf": self.token()}).status_code, 302)


if __name__ == "__main__":
    unittest.main()
