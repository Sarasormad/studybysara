"""Student <-> tutor chat: who can talk to whom, help requests, unread counts and the pages."""
import unittest
from datetime import timedelta

import app as tutor_app
import messages as msgs
from config import MESSAGES_PER_HOUR
from tests.helpers import answer, fresh_db, make_user, noon


def row(db, user_id):
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


class ChatSetup(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.tutor = make_user(self.db, "Tina Tutor", role="tutor")
        self.other_tutor = make_user(self.db, "Oscar Tutor", role="tutor")
        self.kid = make_user(self.db, "Kai Kid", username="kai")
        self.stranger = make_user(self.db, "Sam Stranger", username="sam")
        self.db.execute("UPDATE users SET tutor_id = ? WHERE id = ?", (self.tutor, self.kid))
        self.db.execute("UPDATE users SET tutor_id = ? WHERE id = ?", (self.other_tutor, self.stranger))
        self.db.commit()
        answer(self.db, self.kid, noon(), 30, question="What is 7 x 8?")
        self.problem = self.db.execute("SELECT id FROM problems WHERE created_for = ?", (self.kid,)).fetchone()[0]


class Rules(ChatSetup):
    def test_pairs_only_between_a_student_and_their_tutor(self):
        self.assertEqual(msgs.pair_for(self.db, row(self.db, self.kid)), (self.kid, self.tutor))
        self.assertEqual(msgs.pair_for(self.db, row(self.db, self.tutor), self.kid), (self.kid, self.tutor))
        self.assertIsNone(msgs.pair_for(self.db, row(self.db, self.tutor), self.stranger))
        self.assertIsNone(msgs.pair_for(self.db, row(self.db, self.tutor), self.other_tutor))

    def test_cannot_post_in_someone_elses_conversation(self):
        with self.assertRaises(msgs.MessageError):
            msgs.send(self.db, row(self.db, self.stranger), (self.kid, self.tutor), "hi", noon())

    def test_blank_and_too_long_messages_are_refused(self):
        tutor = row(self.db, self.tutor)
        for body in ("   ", "x" * 1001):
            with self.assertRaises(msgs.MessageError):
                msgs.send(self.db, tutor, (self.kid, self.tutor), body, noon())

    def test_hourly_limit(self):
        tutor = row(self.db, self.tutor)
        for i in range(MESSAGES_PER_HOUR):
            msgs.send(self.db, tutor, (self.kid, self.tutor), f"m{i}", noon() + timedelta(seconds=i))
        with self.assertRaises(msgs.MessageError):
            msgs.send(self.db, tutor, (self.kid, self.tutor), "one more", noon() + timedelta(minutes=5))
        msgs.send(self.db, tutor, (self.kid, self.tutor), "later", noon() + timedelta(hours=2))

    def test_help_request_shows_answer_key_to_tutor_only(self):
        msgs.ask_for_help(self.db, row(self.db, self.kid), self.problem, "stuck", noon())
        tutor_view = msgs.thread(self.db, (self.kid, self.tutor), row(self.db, self.tutor))[0]
        kid_view = msgs.thread(self.db, (self.kid, self.tutor), row(self.db, self.kid))[0]
        self.assertEqual(tutor_view["problem"]["question_text"], "What is 7 x 8?")
        self.assertEqual(tutor_view["problem"]["correct_answer"], "4")
        self.assertIsNone(kid_view["problem"]["correct_answer"])
        self.assertTrue(kid_view["mine"])

    def test_one_help_request_per_problem_and_only_your_own(self):
        kid = row(self.db, self.kid)
        msgs.ask_for_help(self.db, kid, self.problem, "", noon())
        with self.assertRaises(msgs.MessageError):
            msgs.ask_for_help(self.db, kid, self.problem, "again", noon())
        with self.assertRaises(msgs.MessageError):
            msgs.ask_for_help(self.db, row(self.db, self.stranger), self.problem, "", noon())

    def test_unread_counts_and_mark_read(self):
        msgs.send(self.db, row(self.db, self.kid), (self.kid, self.tutor), "hello", noon())
        msgs.send(self.db, row(self.db, self.kid), (self.kid, self.tutor), "anyone?", noon())
        tutor = row(self.db, self.tutor)
        self.assertEqual(msgs.unread_count(self.db, tutor), 2)
        self.assertEqual(msgs.unread_count(self.db, row(self.db, self.kid)), 0)
        self.assertEqual(msgs.conversations(self.db, tutor)[0]["unread"], 2)
        msgs.mark_read(self.db, (self.kid, self.tutor), tutor, noon())
        self.assertEqual(msgs.unread_count(self.db, tutor), 0)


class Pages(ChatSetup):
    def setUp(self):
        super().setUp()
        tutor_app.app.config["TESTING"] = True
        self.client = tutor_app.app.test_client()

    def login_as(self, user_id):
        with self.client.session_transaction() as s:
            s["user_id"] = user_id
            s["_csrf"] = "tok"

    def test_student_goes_straight_to_their_tutor_and_can_send(self):
        self.login_as(self.kid)
        self.assertTrue(self.client.get("/messages").location.endswith(f"/messages/{self.tutor}"))
        resp = self.client.post(f"/messages/{self.tutor}/send", data={"_csrf": "tok", "body": "Hi <b>Tina</b>"},
                                headers={"X-Requested-With": "fetch"})
        data = resp.get_json()
        self.assertIn("Hi &lt;b&gt;Tina&lt;/b&gt;", data["html"])      # escaped, never raw HTML
        self.assertEqual(msgs.unread_count(self.db, row(self.db, self.tutor)), 1)

    def test_student_cannot_open_other_chats(self):
        self.login_as(self.kid)
        self.assertEqual(self.client.get(f"/messages/{self.other_tutor}").status_code, 404)
        self.assertEqual(self.client.post(f"/messages/{self.other_tutor}/send",
                                          data={"_csrf": "tok", "body": "hi"}).status_code, 404)

    def test_tutor_cannot_open_unlinked_student(self):
        self.login_as(self.tutor)
        self.assertEqual(self.client.get(f"/messages/{self.stranger}").status_code, 404)
        self.assertEqual(self.client.get(f"/messages/{self.stranger}/new").status_code, 404)

    def test_tutor_sees_help_request_badge_then_reads_it(self):
        msgs.ask_for_help(self.db, row(self.db, self.kid), self.problem, "I'm stuck", noon())
        self.login_as(self.tutor)
        home = self.client.get("/home").get_data(as_text=True)
        self.assertIn("unread message", home)
        inbox = self.client.get("/messages").get_data(as_text=True)
        self.assertIn("Needs help", inbox)
        chat = self.client.get(f"/messages/{self.kid}").get_data(as_text=True)
        self.assertIn("What is 7 x 8?", chat)
        self.assertIn("answer key", chat)
        self.assertEqual(msgs.unread_count(self.db, row(self.db, self.tutor)), 0)

    def test_view_progress_links_back_to_the_chat(self):
        self.login_as(self.tutor)
        chat = self.client.get(f"/messages/{self.kid}").get_data(as_text=True)
        self.assertIn(f"/dashboard/students/{self.kid}?back=chat", chat)
        page = self.client.get(f"/dashboard/students/{self.kid}?back=chat").get_data(as_text=True)
        self.assertIn("Back to chat", page)
        plain = self.client.get(f"/dashboard/students/{self.kid}").get_data(as_text=True)
        self.assertIn("All students", plain)
        self.assertIn(f'href="/messages/{self.kid}"', plain)          # the Message button

    def test_ask_my_tutor_from_a_question(self):
        self.login_as(self.kid)
        answer(self.db, self.kid, noon(), 30)          # ensure set exists; ask about the first problem
        set_id = self.db.execute("SELECT set_id FROM problems WHERE id = ?", (self.problem,)).fetchone()[0]
        resp = self.client.post(f"/practice/problem/{self.problem}/ask",
                                data={"_csrf": "tok", "note": "help", "back": "feedback"})
        self.assertIn(f"/practice/set/{set_id}", resp.location)
        page = self.client.get(f"/practice/set/{set_id}?answered={self.problem}").get_data(as_text=True)
        self.assertIn("Sent to your tutor", page)

    def test_ask_box_hidden_without_a_tutor(self):
        self.login_as(self.stranger)
        self.db.execute("UPDATE users SET tutor_id = NULL WHERE id = ?", (self.stranger,))
        self.db.commit()
        answer(self.db, self.stranger, noon(), 30)
        pid, set_id = self.db.execute("SELECT id, set_id FROM problems WHERE created_for = ?", (self.stranger,)).fetchone()
        page = self.client.get(f"/practice/set/{set_id}?answered={pid}").get_data(as_text=True)
        self.assertNotIn("Ask my tutor", page)


if __name__ == "__main__":
    unittest.main()
