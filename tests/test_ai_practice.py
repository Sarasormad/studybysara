import copy
import unittest

import anthropic

import ai_practice as ai
import app as tutor_app
import practice as pr
from config import AI_DAILY_LIMIT
from tests.fake_ai import api_error, good_questions, install
from tests.helpers import answer, fresh_db, make_user, noon


class Validation(unittest.TestCase):
    def test_good_questions_pass_and_get_hints(self):
        problems = ai.clean_questions(good_questions(), "Fractions")
        self.assertEqual(len(problems), 5)
        self.assertEqual({p["topic"] for p in problems}, {"Fractions"})
        self.assertIsNone(problems[1]["choices"])
        self.assertIn("number", problems[1]["hint"])

    def test_extra_questions_are_trimmed_but_too_few_fail(self):
        self.assertEqual(len(ai.clean_questions(good_questions(7), "T")), 5)
        with self.assertRaises(ai.InvalidQuestions):
            ai.clean_questions(good_questions(4), "T")

    def bad(self, change):
        data = good_questions()
        change(data["questions"])
        with self.assertRaises(ai.InvalidQuestions):
            ai.clean_questions(data, "T")

    def test_rejects_answer_not_among_choices(self):
        self.bad(lambda q: q[0].update(correct_answer="99"))

    def test_rejects_equivalent_choices(self):
        # "1/2" and "0.5" would both grade as correct, so the question is unusable.
        self.bad(lambda q: q[0].update(choices=["1/2", "0.5", "3", "4"], correct_answer="1/2"))

    def test_rejects_wrong_number_of_choices(self):
        self.bad(lambda q: q[0].update(choices=["2", "3", "4"]))

    def test_rejects_short_answers_that_are_not_bare_values(self):
        self.bad(lambda q: q[1].update(correct_answer="x = 6"))
        self.bad(lambda q: q[1].update(correct_answer="about six apples in total"))

    def test_rejects_latex_and_empty_text(self):
        self.bad(lambda q: q[0].update(question=r"What is \frac{1}{2}?"))
        self.bad(lambda q: q[2].update(explanation=""))
        self.bad(lambda q: q[3].update(answer_type="essay"))

    def test_word_answers_are_allowed(self):
        data = good_questions()
        data["questions"][1].update(question="Is 7 prime or composite?", correct_answer="prime")
        problems = ai.clean_questions(data, "T")
        self.assertIn("word", problems[1]["hint"])


class Generation(unittest.TestCase):
    def test_request_shape(self):
        fake = install(self, good_questions())
        ai.generate_topic_questions("fractions", "Grade 6")
        req = fake.requests[0]
        self.assertEqual(req["model"], "claude-opus-5-5")
        self.assertEqual(req["fallbacks"], "default")
        self.assertEqual(req["betas"], [ai.FALLBACK_BETA])
        self.assertEqual(req["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(req["output_config"]["effort"], "medium")
        self.assertIn("Fractions", req["messages"][0]["content"])
        self.assertIn("Grade 6", req["messages"][0]["content"])

    def test_bad_json_is_retried_once(self):
        fake = install(self, "not json {", good_questions())
        self.assertEqual(len(ai.generate_topic_questions("ratios", "Grade 7")), 5)
        self.assertEqual(len(fake.requests), 2)

    def test_two_bad_answers_give_a_friendly_error(self):
        broken = copy.deepcopy(good_questions())
        broken["questions"][0]["correct_answer"] = "not a choice"
        fake = install(self, broken, "garbage")
        with self.assertRaises(ai.AIError) as err:
            ai.generate_topic_questions("ratios", "Grade 7")
        self.assertIn("try again", str(err.exception))
        self.assertEqual(len(fake.requests), 2)

    def test_refusal_and_api_errors_are_friendly_and_not_retried(self):
        cases = [(("refusal", None), "different topic"),
                 (api_error(anthropic.AuthenticationError, 401), "isn't set up"),
                 (api_error(anthropic.RateLimitError, 429), "Try again in a minute"),
                 (api_error(anthropic.InternalServerError, 500), "had a problem")]
        for item, message in cases:
            with self.subTest(message=message):
                fake = install(self, item)
                with self.assertRaises(ai.AIError) as err:
                    ai.generate_topic_questions("geometry", "Grade 8")
                self.assertIn(message, str(err.exception))
                self.assertEqual(len(fake.requests), 1)

    def test_unknown_topic(self):
        with self.assertRaises(ai.AIError):
            ai.generate_topic_questions("cooking", "Grade 8")

    def test_topics_fit_the_grade(self):
        fits, others, test_prep = ai.topics_for("Grade 4")
        names = [t["name"] for t in fits]
        self.assertIn("Fractions", names)
        self.assertNotIn("Trigonometry", names)
        self.assertIn("Trigonometry", [t["name"] for t in others])
        self.assertTrue(all(t["name"].startswith(("SAT", "ACT")) for t in test_prep))
        fits11, _, _ = ai.topics_for("Grade 11")
        self.assertIn("Trigonometry", [t["name"] for t in fits11])


class TopicRoutes(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        tutor_app.app.config["TESTING"] = True
        self.client = tutor_app.app.test_client()
        self.kid = make_user(self.db, "Kid", username="kid", grade="Grade 7")
        with self.client.session_transaction() as s:
            s["user_id"] = self.kid

    def token(self):
        self.client.get("/practice")
        with self.client.session_transaction() as s:
            return s["_csrf"]

    def start(self, topic="ratios"):
        return self.client.post("/practice/topic/start", data={"_csrf": self.token(), "topic": topic})

    def test_start_creates_a_topic_set_without_leaking_answers(self):
        install(self, good_questions())
        resp = self.start()
        set_id = int(resp.headers["Location"].rsplit("/", 1)[1])
        pset = self.db.execute("SELECT * FROM problem_sets WHERE id = ?", (set_id,)).fetchone()
        self.assertEqual((pset["source"], pset["topic"]), ("topic", "Ratios & proportions"))
        html = self.client.get(f"/practice/set/{set_id}").get_data(as_text=True)
        self.assertIn("AI-generated", html)
        self.assertIn("Report a problem", html)
        self.assertNotIn("0 + 2 = 2.", html)                         # the explanation stays on the server
        self.assertEqual(pr.ai_generations_left(self.db, self.kid, pr.utcnow()), AI_DAILY_LIMIT - 1)

    def test_daily_limit_blocks_without_calling_the_api(self):
        for _ in range(AI_DAILY_LIMIT):
            pr.record_ai_generation(self.db, self.kid, "topic", "Ratios", None, pr.utcnow())
        fake = install(self)                                          # no responses queued
        html = self.client.post("/practice/topic/start", data={"_csrf": self.token(), "topic": "ratios"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("used all 20 AI question sets", html)
        self.assertEqual(fake.requests, [])

    def test_failed_generation_shows_message_and_uses_no_credit(self):
        install(self, api_error(anthropic.InternalServerError, 500))
        html = self.client.post("/practice/topic/start", data={"_csrf": self.token(), "topic": "ratios"},
                                follow_redirects=True).get_data(as_text=True)
        self.assertIn("had a problem", html)
        self.assertEqual(pr.ai_generations_left(self.db, self.kid, pr.utcnow()), AI_DAILY_LIMIT)

    def test_tutors_cannot_generate(self):
        tutor = make_user(self.db, "Tutor", role="tutor")
        with self.client.session_transaction() as s:
            s["user_id"] = tutor
        fake = install(self)
        self.start()
        self.assertEqual(fake.requests, [])


class TopicPointsAndReports(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.kid = make_user(self.db, "Kid", username="kid")

    def test_topic_answers_count_for_time_but_not_points(self):
        result = answer(self.db, self.kid, noon(), 120, source="topic", topic="Ratios & proportions")
        self.assertEqual(result["points"], 0)
        self.assertEqual(pr.seconds_on_day(self.db, self.kid, noon().date()), 120)
        self.assertEqual(pr.topic_accuracy(self.db, self.kid)[0]["topic"], "Ratios & proportions")

    def test_reports_only_on_own_ai_questions(self):
        answer(self.db, self.kid, noon(), 30, source="topic")
        answer(self.db, self.kid, noon(), 30, source="fundamentals")
        topic_q, fund_q = [r[0] for r in self.db.execute("SELECT id FROM problems ORDER BY id")]
        other = make_user(self.db, "Other")
        self.assertTrue(pr.report_problem(self.db, self.kid, topic_q, "wrong_answer", "It's 5", noon()))
        self.assertTrue(pr.report_problem(self.db, self.kid, topic_q, "wrong_answer", None, noon()))  # no duplicate row
        self.assertFalse(pr.report_problem(self.db, self.kid, fund_q, "wrong_answer", None, noon()))
        self.assertFalse(pr.report_problem(self.db, other, topic_q, "wrong_answer", None, noon()))
        self.assertFalse(pr.report_problem(self.db, self.kid, topic_q, "made_up_reason", None, noon()))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM problem_reports").fetchone()[0], 1)
        self.assertEqual(pr.reported_ids(self.db, self.kid, [topic_q, fund_q]), {topic_q})


if __name__ == "__main__":
    unittest.main()
