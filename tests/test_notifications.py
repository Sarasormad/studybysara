"""Email notifications: help requests, daily reminders (with backoff) and low-accuracy alerts."""
import email
import glob
import os
import shutil
import unittest
from datetime import timedelta
from email import policy

import app as tutor_app
import mailer
import messages as msgs
import notify
from tests.helpers import answer, fresh_db, make_user, noon, practice_minutes

AFTERNOON = 4      # noon() + 4h = 4 PM Pacific, inside the reminder window


def outbox():
    files = sorted(glob.glob(os.path.join(mailer.OUTBOX, "*.eml")))
    out = []
    for f in files:
        with open(f, "rb") as fh:
            out.append(email.message_from_binary_file(fh, policy=policy.default))
    return out


def body(msg):
    return msg.get_body(preferencelist=("plain",)).get_content()


class Base(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(mailer.OUTBOX, ignore_errors=True)
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        tutor_app.app.config["TESTING"] = True
        self.tutor = make_user(self.db, "Tina Tutor", role="tutor")
        self.kid = make_user(self.db, "Kai Kid", username="kai")
        self.db.execute("UPDATE users SET tutor_id = ? WHERE id = ?", (self.tutor, self.kid))
        self.db.execute("UPDATE users SET created_at = '2026-10-01 12:00:00'")
        self.db.commit()

    def row(self, user_id):
        return self.db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

    def run_at(self, when):
        with tutor_app.app.test_request_context(base_url="http://testserver"):
            return notify.run_due(self.db, when)


class HelpEmails(Base):
    def test_tutor_gets_an_email_with_the_question_and_a_reply_link(self):
        answer(self.db, self.kid, noon(), 30, question="What is 7 x 8?")
        pid = self.db.execute("SELECT id FROM problems").fetchone()[0]
        client = tutor_app.app.test_client()
        with client.session_transaction() as s:
            s["user_id"], s["_csrf"] = self.kid, "tok"
        client.post(f"/practice/problem/{pid}/ask", data={"_csrf": "tok", "note": "I'm stuck"})
        [msg] = outbox()
        self.assertEqual(msg["To"], "tinatutor@example.test")
        self.assertIn("Kai Kid", msg["Subject"])
        text = body(msg)
        self.assertIn("What is 7 x 8?", text)
        self.assertIn("I'm stuck", text)
        self.assertIn(f"http://testserver/messages/{self.kid}", text)
        self.assertNotIn("Answer: 4", text)
        self.assertIn("/notifications/unsubscribe/", msg["List-Unsubscribe"])

    def test_no_email_when_the_tutor_turned_it_off(self):
        self.db.execute("UPDATE users SET notify_help = 0 WHERE id = ?", (self.tutor,))
        self.db.commit()
        answer(self.db, self.kid, noon(), 30)
        pid = self.db.execute("SELECT id FROM problems").fetchone()[0]
        mid = msgs.ask_for_help(self.db, self.row(self.kid), pid, "", noon())
        with tutor_app.app.test_request_context():
            self.assertFalse(notify.help_requested(self.db, self.row(self.kid), mid, noon()))
        self.assertEqual(outbox(), [])


class Reminders(Base):
    def test_only_in_the_afternoon_window_and_once_a_day(self):
        answer(self.db, self.kid, noon(0), 30)
        self.assertEqual(self.run_at(noon(1))["reminder"], 0)                      # noon: too early
        self.assertEqual(self.run_at(noon(1) + timedelta(hours=9))["reminder"], 0)  # 9 PM: too late
        self.assertEqual(self.run_at(noon(1) + timedelta(hours=AFTERNOON))["reminder"], 1)
        self.assertEqual(self.run_at(noon(1) + timedelta(hours=AFTERNOON + 1))["reminder"], 0)
        self.assertEqual(len(outbox()), 1)

    def test_no_reminder_once_todays_goal_is_done(self):
        practice_minutes(self.db, self.kid, noon(1) - timedelta(hours=2), 10)
        self.assertEqual(self.run_at(noon(1) + timedelta(hours=AFTERNOON))["reminder"], 0)

    def test_streak_and_almost_done_wording(self):
        practice_minutes(self.db, self.kid, noon(0), 10)                            # 1-day streak
        self.run_at(noon(1) + timedelta(hours=AFTERNOON))
        self.assertIn("1-day streak", outbox()[-1]["Subject"])
        practice_minutes(self.db, self.kid, noon(2), 4)                             # started, not done
        self.run_at(noon(2) + timedelta(hours=AFTERNOON))
        self.assertIn("minutes from today", outbox()[-1]["Subject"])

    def test_backs_off_then_stops(self):
        answer(self.db, self.kid, noon(0), 30)
        sent_days = [d for d in range(1, 16)
                     if self.run_at(noon(d) + timedelta(hours=AFTERNOON))["reminder"]]
        self.assertEqual(sent_days, [1, 2, 4, 7, 10])

    def test_turned_off_and_tutors_never_get_reminders(self):
        self.db.execute("UPDATE users SET notify_reminders = 0 WHERE id = ?", (self.kid,))
        self.db.commit()
        self.assertEqual(self.run_at(noon(1) + timedelta(hours=AFTERNOON))["reminder"], 0)


class LowAccuracy(Base):
    def test_alert_once_a_week_with_the_numbers(self):
        for i in range(10):
            answer(self.db, self.kid, noon() + timedelta(minutes=i), 30, correct=False, topic="Fractions")
        practice_minutes(self.db, self.kid, noon(1), 10)        # keeps reminders out of the way
        self.assertEqual(self.run_at(noon(1) + timedelta(hours=AFTERNOON))["low_accuracy"], 1)
        alert = [m for m in outbox() if m["To"] == "tinatutor@example.test"][-1]
        self.assertIn("Kai Kid", alert["Subject"])
        self.assertIn("% (", body(alert))
        self.assertEqual(self.run_at(noon(3) + timedelta(hours=AFTERNOON))["low_accuracy"], 0)   # within a week
        self.assertEqual(self.run_at(noon(9) + timedelta(hours=AFTERNOON))["low_accuracy"], 1)   # a week later

    def test_no_alert_with_too_few_answers_or_good_accuracy(self):
        for i in range(9):
            answer(self.db, self.kid, noon() + timedelta(minutes=i), 30, correct=False)
        self.assertEqual(self.run_at(noon() + timedelta(hours=AFTERNOON))["low_accuracy"], 0)


class Unsubscribe(Base):
    def test_link_turns_off_only_that_kind(self):
        client = tutor_app.app.test_client()
        with tutor_app.app.test_request_context():
            token = notify.unsubscribe_token(self.kid, "reminder")
        self.assertIn("Turn off daily practice reminders", client.get(f"/notifications/unsubscribe/{token}").get_data(as_text=True))
        with client.session_transaction() as s:
            s["_csrf"] = "tok"
        client.post(f"/notifications/unsubscribe/{token}", data={"_csrf": "tok"})
        self.assertEqual(self.row(self.kid)["notify_reminders"], 0)
        self.assertEqual(client.get("/notifications/unsubscribe/forged-token").status_code, 404)

    def test_settings_on_edit_profile(self):
        client = tutor_app.app.test_client()
        with client.session_transaction() as s:
            s["user_id"], s["_csrf"] = self.tutor, "tok"
        client.post("/profile/edit", data={"_csrf": "tok", "name": "Tina", "subject": "Math", "notify_help": "1"})
        r = self.row(self.tutor)
        self.assertEqual((r["notify_help"], r["notify_alerts"]), (1, 0))



class Mailer(unittest.TestCase):
    def test_real_smtp_is_never_tried_for_test_addresses(self):
        from unittest import mock
        with mock.patch.dict(os.environ, {"SMTP_HOST": "smtp.invalid"}), mock.patch("smtplib.SMTP") as smtp:
            self.assertFalse(mailer.send("kid@demo.example.test", "Hi", "text"))
            smtp.assert_not_called()


if __name__ == "__main__":
    unittest.main()
