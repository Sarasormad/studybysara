import unittest
from datetime import date, datetime, timedelta, timezone

import streaks
from config import DAILY_GOAL_SECONDS, MAX_SECONDS_PER_QUESTION
from tests.helpers import answer, fresh_db, make_user, noon, practice_minutes

import practice as pr


class ActiveTime(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.user = make_user(self.db)

    def test_time_per_question_is_capped_at_3_minutes(self):
        answer(self.db, self.user, noon(), seconds=45 * 60)        # tab left open for 45 minutes
        spent = self.db.execute("SELECT time_spent_seconds FROM attempts").fetchone()[0]
        self.assertEqual(spent, MAX_SECONDS_PER_QUESTION)

    def test_shown_but_unanswered_questions_add_no_time(self):
        pr.create_set(self.db, self.user, "fundamentals", [{
            "topic": "t", "question_text": "q", "answer_type": "short_answer", "correct_answer": "1"}], noon())
        pid = self.db.execute("SELECT id FROM problems").fetchone()[0]
        pr.mark_served(self.db, pid, noon())
        self.assertEqual(pr.seconds_on_day(self.db, self.user, streaks.practice_day(noon())), 0)

    def test_day_boundary_uses_los_angeles_time(self):
        late_night = datetime(2026, 11, 9, 7, 30, tzinfo=timezone.utc)   # 11:30 PM Nov 8 in LA
        self.assertEqual(streaks.practice_day(late_night), date(2026, 11, 8))


class DailyGoal(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.user = make_user(self.db)

    def streak_points(self):
        return [r["points"] for r in self.db.execute(
            "SELECT points FROM points_ledger WHERE reason = 'streak' ORDER BY practice_day")]

    def test_just_under_10_minutes_does_not_count(self):
        start = noon()
        for i in range(3):                                          # 3 x 180s = 540s
            answer(self.db, self.user, start + timedelta(minutes=3 * i), 180)
        answer(self.db, self.user, start + timedelta(minutes=10), 59)  # 599s total
        self.assertEqual(pr.seconds_on_day(self.db, self.user, streaks.practice_day(start)), DAILY_GOAL_SECONDS - 1)
        self.assertEqual(self.streak_points(), [])
        self.assertEqual(pr.practice_status(self.db, self.user, start)["current_streak"], 0)

    def test_crossing_10_minutes_awards_once(self):
        start = noon()
        for i in range(3):
            answer(self.db, self.user, start + timedelta(minutes=3 * i), 180)
        result = answer(self.db, self.user, start + timedelta(minutes=10), 61)   # 601s: crosses the goal
        self.assertEqual(result["streak_bonus"], 2)                 # day 1 of a streak = 2 points
        later = answer(self.db, self.user, start + timedelta(minutes=20), 180)  # keep practicing
        self.assertEqual(later["streak_bonus"], 0)                  # no second bonus the same day
        self.assertEqual(self.streak_points(), [2])

    def test_missed_day_breaks_the_streak(self):
        practice_minutes(self.db, self.user, noon(0), 10)           # day 1 -> 2
        practice_minutes(self.db, self.user, noon(1), 10)           # day 2 -> 3
        practice_minutes(self.db, self.user, noon(2), 5)            # only 5 minutes: doesn't count
        results = practice_minutes(self.db, self.user, noon(3), 10) # streak starts over
        self.assertEqual(sum(r["streak_bonus"] for r in results), 2)
        self.assertEqual(self.streak_points(), [2, 3, 2])
        status = pr.practice_status(self.db, self.user, noon(3))
        self.assertEqual((status["current_streak"], status["best_streak"]), (1, 2))

    def test_streak_alive_until_the_day_ends(self):
        practice_minutes(self.db, self.user, noon(0), 10)
        status = pr.practice_status(self.db, self.user, noon(1))   # next day, not practiced yet
        self.assertEqual(status["current_streak"], 1)
        self.assertEqual(status["bonus_if_finished"], 3)
        status = pr.practice_status(self.db, self.user, noon(2))   # skipped a whole day
        self.assertEqual(status["current_streak"], 0)
        self.assertEqual(status["bonus_if_finished"], 2)

    def test_bonus_caps_at_10_after_day_9(self):
        for day in range(12):
            practice_minutes(self.db, self.user, noon(day), 10)
        points = self.streak_points()
        self.assertEqual(points, [2, 3, 4, 5, 6, 7, 8, 9, 10, 10, 10, 10])
        self.assertEqual(sum(points[:3]), 9)                         # 3-day streak = 9 points
        self.assertEqual(sum(points[:9]), 54)                        # 9-day streak = 54 points


class FundamentalsPoints(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.user = make_user(self.db)

    def test_points_only_for_correct_fundamentals(self):
        self.assertEqual(answer(self.db, self.user, noon(), 30)["points"], 10)
        self.assertEqual(answer(self.db, self.user, noon(), 30, correct=False)["points"], 0)
        self.assertEqual(answer(self.db, self.user, noon(), 30, source="topic")["points"], 0)
        self.assertEqual(answer(self.db, self.user, noon(), 30, source="photo")["points"], 0)
        total = self.db.execute("SELECT SUM(points) FROM points_ledger").fetchone()[0]
        self.assertEqual(total, 10)

    def test_cannot_answer_twice_or_answer_someone_elses_question(self):
        answer(self.db, self.user, noon(), 30)
        pid = self.db.execute("SELECT id FROM problems").fetchone()[0]
        with self.assertRaises(pr.AnswerError):
            pr.record_attempt(self.db, self.user, pid, "4", noon())
        other = make_user(self.db, name="Other")
        with self.assertRaises(pr.AnswerError):
            pr.record_attempt(self.db, other, pid, "4", noon())


if __name__ == "__main__":
    unittest.main()
