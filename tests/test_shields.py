"""Phase C: streak shields and healthy stopping points."""
import unittest
from datetime import date, timedelta
from unittest import mock

import app as tutor_app
import practice as pr
import streaks
from config import MAX_SHIELDS
from tests.helpers import fresh_db, make_user, noon, practice_minutes

D = [date(2026, 11, 2) + timedelta(days=i) for i in range(20)]


class ShieldRules(unittest.TestCase):
    def test_shielded_days_link_a_streak_but_dont_count(self):
        real, shielded = {D[0], D[1], D[3]}, {D[2]}
        self.assertEqual(streaks.streak_ending(real, D[3]), 1)              # broken without the shield
        self.assertEqual(streaks.streak_ending(real, D[3], shielded), 3)    # 3 real days, 1 shielded
        self.assertEqual(streaks.best_streak(real, shielded), 3)

    def test_which_missed_days_a_shield_covers(self):
        played = {D[0]}
        self.assertEqual(streaks.days_a_shield_would_cover(played, D[2], 1), {D[1]})
        self.assertEqual(streaks.days_a_shield_would_cover(played, D[3], 1), set())          # 2 missed, 1 shield
        self.assertEqual(streaks.days_a_shield_would_cover(played, D[3], 2), {D[1], D[2]})
        self.assertEqual(streaks.days_a_shield_would_cover(played, D[1], 1), set())          # nothing missed
        self.assertEqual(streaks.days_a_shield_would_cover(set(), D[3], 2), set())          # no history
        self.assertEqual(streaks.days_a_shield_would_cover(played, D[2], 0), set())          # no shields


class EarningAndUsingShields(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.kid = make_user(self.db, "Kid", username="kid")

    def play_days(self, days):
        results = {}
        for d in days:
            results[d] = practice_minutes(self.db, self.kid, noon(d), 10)
        return results

    def streak_bonus(self, results):
        return sum(r["streak_bonus"] for r in results)

    def test_a_shield_is_earned_every_7th_day_up_to_the_max(self):
        results = self.play_days(range(7))
        self.assertTrue(any(r["shield_earned"] for r in results[6]))
        self.assertEqual(pr.shields_held(self.db, self.kid), 1)
        self.play_days(range(7, 21))                                         # days 14 and 21
        self.assertEqual(pr.shields_held(self.db, self.kid), MAX_SHIELDS)

    def test_a_shield_saves_the_streak_after_one_missed_day(self):
        self.play_days(range(7))                                             # 7-day streak + shield
        status = pr.practice_status(self.db, self.kid, noon(8))             # missed day 7, back on day 8
        self.assertTrue(status["shield_protecting"])
        self.assertEqual(status["current_streak"], 7)
        results = self.play_days([8])[8]
        self.assertEqual(self.streak_bonus(results), streaks.streak_points(8))   # day 8 of the streak
        self.assertEqual(pr.shields_held(self.db, self.kid), 0)
        self.assertEqual(pr.shielded_days(self.db, self.kid), {streaks.practice_day(noon(7))})
        status = pr.practice_status(self.db, self.kid, noon(8))
        self.assertEqual((status["current_streak"], status["best_streak"]), (8, 8))
        self.assertFalse(status["shield_protecting"])

    def test_missing_more_days_than_shields_still_breaks_it(self):
        self.play_days(range(7))                                             # 1 shield
        status = pr.practice_status(self.db, self.kid, noon(9))             # missed days 7 and 8
        self.assertFalse(status["shield_protecting"])
        self.assertEqual(status["current_streak"], 0)
        results = self.play_days([9])[9]
        self.assertEqual(self.streak_bonus(results), 2)                      # starts over
        self.assertEqual(pr.shields_held(self.db, self.kid), 1)              # shield kept for later

    def test_no_shield_no_rescue(self):
        self.play_days([0, 1, 2])
        self.assertEqual(self.streak_bonus(self.play_days([4])[4]), 2)


class StoppingPoints(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        tutor_app.app.config["TESTING"] = True
        self.client = tutor_app.app.test_client()
        self.kid = make_user(self.db, "Kid", username="kid")
        with self.client.session_transaction() as s:
            s["user_id"] = self.kid
        clock = mock.patch.object(pr, "utcnow", return_value=noon(3))   # never near midnight
        clock.start()
        self.addCleanup(clock.stop)

    def test_done_for_today_after_the_goal(self):
        self.assertNotIn("done for today", self.client.get("/practice").get_data(as_text=True))
        practice_minutes(self.db, self.kid, noon(3) - timedelta(minutes=15), 10)
        html = self.client.get("/practice").get_data(as_text=True)
        self.assertIn("You're done for today!", html)
        self.assertIn("I'm done for today", html)

    def test_break_nudge_after_a_long_session(self):
        practice_minutes(self.db, self.kid, noon(3) - timedelta(minutes=50), 45)
        html = self.client.get("/practice").get_data(as_text=True)
        self.assertIn("You've practiced 45 minutes today", html)
        self.assertIn("after a break", html)

    def test_shield_shows_on_the_dashboard(self):
        html = self.client.get("/dashboard").get_data(as_text=True)
        self.assertIn("0 of 2 shields", html)
        self.assertIn("next in 7 days", html)


if __name__ == "__main__":
    unittest.main()
