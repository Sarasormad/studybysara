import unittest
from datetime import timedelta

import practice as pr
import streaks
from tests.helpers import fresh_db, make_user, noon


def give_points(db, user_id, points, when):
    db.execute("INSERT INTO points_ledger (user_id, reason, points, practice_day, created_at) "
               "VALUES (?, 'fundamentals', ?, ?, ?)",
               (user_id, points, streaks.practice_day(when).isoformat(), pr.to_db(when)))
    db.commit()


class Leaderboard(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.now = noon(3)                                           # Thursday of the test week
        self.week = streaks.week_start_utc(self.now)

    def test_ranks_ties_and_username_only(self):
        a = make_user(self.db, "Ana Real", username="ana")
        b = make_user(self.db, "Ben Real", username="ben")
        c = make_user(self.db, "Cy Real", username="cy")
        give_points(self.db, a, 30, self.now)
        give_points(self.db, b, 50, self.now)
        give_points(self.db, c, 30, self.now)
        rows, me = pr.leaderboard(self.db, self.week, viewer_id=c)
        self.assertEqual([(r["rank"], r["username"], r["points"]) for r in rows],
                         [(1, "ben", 50), (2, "ana", 30), (2, "cy", 30)])
        self.assertTrue(rows[2]["is_me"])
        self.assertIsNone(me)
        for r in rows:                                                # nothing personal leaks out
            self.assertEqual(set(r), {"rank", "username", "points", "is_me"})

    def test_hides_students_without_username_and_tutors(self):
        named = make_user(self.db, "Named", username="named")
        anon = make_user(self.db, "Anon")
        tutor = make_user(self.db, "Tutor", role="tutor", username="tutor_x")
        for uid in (named, anon, tutor):
            give_points(self.db, uid, 10, self.now)
        rows, _ = pr.leaderboard(self.db, None)
        self.assertEqual([r["username"] for r in rows], ["named"])

    def test_this_week_resets_monday_but_all_time_keeps_everything(self):
        u = make_user(self.db, "U", username="u")
        give_points(self.db, u, 40, self.week - timedelta(minutes=1))  # Sunday 11:59 PM last week
        give_points(self.db, u, 10, self.week)                         # Monday 12:00 AM
        week_rows, _ = pr.leaderboard(self.db, self.week)
        all_rows, _ = pr.leaderboard(self.db, None)
        self.assertEqual(week_rows[0]["points"], 10)
        self.assertEqual(all_rows[0]["points"], 50)

    def test_my_rank_shown_when_outside_the_top(self):
        users = [make_user(self.db, f"S{i}", username=f"s{i}") for i in range(5)]
        for i, uid in enumerate(users):
            give_points(self.db, uid, 100 - i * 10, self.now)
        rows, me = pr.leaderboard(self.db, None, viewer_id=users[4], limit=3)
        self.assertEqual(len(rows), 3)
        self.assertEqual((me["rank"], me["username"], me["points"]), (5, "s4", 60))


if __name__ == "__main__":
    unittest.main()
