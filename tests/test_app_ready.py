"""App-store readiness: installable web app files, health check, security headers and deleting an account."""
import json
import unittest

from werkzeug.security import generate_password_hash

import app as tutor_app
import messages as msgs
import shop
from tests.helpers import answer, fresh_db, make_user, noon


class InstallableApp(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.client = tutor_app.app.test_client()

    def test_manifest_service_worker_offline_and_health(self):
        m = self.client.get("/manifest.webmanifest")
        self.assertEqual(m.mimetype, "application/manifest+json")
        data = json.loads(m.get_data(as_text=True))
        self.assertEqual(data["display"], "standalone")
        self.assertTrue(any(i["purpose"] == "maskable" for i in data["icons"]))
        m.close()
        sw = self.client.get("/sw.js")
        self.assertEqual(sw.headers["Cache-Control"], "no-cache")
        self.assertIn("offline", sw.get_data(as_text=True))
        sw.close()
        self.assertIn("You're offline", self.client.get("/offline").get_data(as_text=True))
        self.assertEqual(self.client.get("/healthz").get_json(), {"ok": True})
        for icon in ("icon-192.png", "icon-512.png", "maskable-512.png", "apple-touch-icon.png"):
            r = self.client.get(f"/static/icons/{icon}")
            self.assertEqual(r.status_code, 200, icon)
            r.close()

    def test_pages_link_the_manifest_and_send_security_headers(self):
        r = self.client.get("/login")
        html = r.get_data(as_text=True)
        self.assertIn('rel="manifest"', html)
        self.assertIn("apple-touch-icon", html)
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")


class DeleteAccount(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        tutor_app.app.config["TESTING"] = True
        self.client = tutor_app.app.test_client()
        self.tutor = make_user(self.db, "Tina Tutor", role="tutor")
        self.kid = make_user(self.db, "Kai Kid", username="kai")
        self.other = make_user(self.db, "Ola Other", username="ola")
        self.db.execute("UPDATE users SET tutor_id = ? WHERE id IN (?, ?)", (self.tutor, self.kid, self.other))
        self.db.execute("UPDATE users SET password_hash = ?", (generate_password_hash("pw-123456"),))
        self.db.commit()
        answer(self.db, self.kid, noon(), 30)
        answer(self.db, self.other, noon(), 30)
        kid = self.db.execute("SELECT * FROM users WHERE id = ?", (self.kid,)).fetchone()
        msgs.send(self.db, kid, (self.kid, self.tutor), "hi", noon())

    def login(self, user_id):
        with self.client.session_transaction() as s:
            s["user_id"], s["_csrf"] = user_id, "tok"

    def count(self, sql, *args):
        return self.db.execute(sql, args).fetchone()[0]

    def test_wrong_password_deletes_nothing(self):
        self.login(self.kid)
        self.client.post("/profile/delete", data={"_csrf": "tok", "password": "nope"})
        self.assertEqual(self.count("SELECT COUNT(*) FROM users WHERE id = ?", self.kid), 1)

    def test_student_delete_removes_all_their_data_only(self):
        self.login(self.kid)
        self.assertIn("This can't be undone", self.client.get("/profile/delete").get_data(as_text=True))
        resp = self.client.post("/profile/delete", data={"_csrf": "tok", "password": "pw-123456"})
        self.assertTrue(resp.location.endswith("/login"))
        for table, col in (("users", "id"), ("attempts", "user_id"), ("problems", "created_for"),
                           ("problem_sets", "user_id"), ("points_ledger", "user_id"), ("messages", "student_id")):
            self.assertEqual(self.count(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", self.kid), 0, table)
        self.assertEqual(self.count("SELECT COUNT(*) FROM attempts WHERE user_id = ?", self.other), 1)   # others untouched
        with self.client.session_transaction() as s:
            self.assertNotIn("user_id", s)                                                              # logged out

    def test_tutor_delete_unlinks_students(self):
        self.login(self.tutor)
        self.assertIn("2 students will be unlinked", self.client.get("/profile/delete").get_data(as_text=True))
        self.client.post("/profile/delete", data={"_csrf": "tok", "password": "pw-123456"})
        self.assertEqual(self.count("SELECT COUNT(*) FROM users WHERE tutor_id IS NOT NULL"), 0)
        self.assertEqual(self.count("SELECT COUNT(*) FROM users WHERE role = 'student'"), 2)
        self.assertEqual(self.count("SELECT COUNT(*) FROM messages"), 0)


if __name__ == "__main__":
    unittest.main()
