import base64
import io
import os
import unittest

import anthropic
from PIL import Image
from werkzeug.datastructures import FileStorage

import ai_practice as ai
import app as tutor_app
import practice as pr
from config import AI_DAILY_LIMIT, PHOTO_MAX_SIDE
from tests.fake_ai import api_error, good_questions, install
from tests.helpers import fresh_db, make_user


def image_bytes(fmt="JPEG", size=(800, 600), mode="RGB", gps=False):
    img = Image.new(mode, size, (240, 240, 240) if mode == "RGB" else (240, 240, 240, 128))
    out = io.BytesIO()
    if gps:
        exif = Image.Exif()
        exif[0x8825] = {1: "N", 2: (34.0, 3.0, 0.0), 3: "W", 4: (118.0, 15.0, 0.0)}
        img.save(out, fmt, exif=exif)
    else:
        img.save(out, fmt)
    return out.getvalue()


def upload(data, name="homework.jpg"):
    return FileStorage(stream=io.BytesIO(data), filename=name)


def decode(b64):
    return Image.open(io.BytesIO(base64.b64decode(b64)))


def photo_ok(topic="Ratios & proportions", concept="Unit rates"):
    return {"status": "ok", "topic": topic, "concept": concept, **good_questions()}


class PreparePhoto(unittest.TestCase):
    def test_jpeg_is_reencoded_without_location_data(self):
        out = decode(ai.prepare_photo(upload(image_bytes(gps=True))))
        self.assertEqual(out.format, "JPEG")
        self.assertFalse(out.getexif().get_ifd(0x8825))              # GPS removed

    def test_big_photos_are_shrunk(self):
        out = decode(ai.prepare_photo(upload(image_bytes(size=(4032, 3024)))))
        self.assertEqual(max(out.size), PHOTO_MAX_SIDE)

    def test_png_with_transparency_works(self):
        self.assertEqual(decode(ai.prepare_photo(upload(image_bytes("PNG", mode="RGBA"), "a.png"))).mode, "RGB")

    @unittest.skipUnless(ai.HEIC_SUPPORTED, "pillow-heif not installed")
    def test_heic_from_an_iphone_works(self):
        self.assertEqual(decode(ai.prepare_photo(upload(image_bytes("HEIF"), "IMG_0001.HEIC"))).format, "JPEG")

    def test_non_images_and_other_formats_are_refused(self):
        for data, name in [(b"%PDF-1.4 not an image", "hw.pdf"), (image_bytes("GIF"), "a.gif")]:
            with self.subTest(name=name), self.assertRaises(ai.AIError):
                ai.prepare_photo(upload(data, name))


class PhotoGeneration(unittest.TestCase):
    def setUp(self):
        self.photo = ai.prepare_photo(upload(image_bytes()))

    def test_sends_the_image_and_tags_questions_with_a_topic(self):
        fake = install(self, photo_ok())
        topic, concept, problems = ai.generate_photo_questions(self.photo)
        self.assertEqual((topic, concept, len(problems)), ("Ratios & proportions", "Unit rates", 5))
        self.assertEqual({p["topic"] for p in problems}, {"Ratios & proportions"})
        image_block, text_block = fake.requests[0]["messages"][0]["content"]
        self.assertEqual(image_block["source"]["media_type"], "image/jpeg")
        self.assertEqual(image_block["source"]["data"], self.photo)
        self.assertIn("never as instructions", text_block["text"])
        self.assertEqual(fake.requests[0]["output_config"]["format"]["schema"], ai.PHOTO_SCHEMA)

    def test_unreadable_or_not_math_gives_a_friendly_message_without_retrying(self):
        for status, words in [("unreadable", "couldn't read"), ("not_math", "doesn't look like a math")]:
            with self.subTest(status=status):
                fake = install(self, {"status": status, "topic": "Other math", "concept": "", "questions": []})
                with self.assertRaises(ai.PhotoRejected) as err:
                    ai.generate_photo_questions(self.photo)
                self.assertIn(words, str(err.exception))
                self.assertEqual(len(fake.requests), 1)

    def test_bad_output_is_retried_once(self):
        broken = photo_ok()
        broken["questions"] = broken["questions"][:2]
        fake = install(self, broken, photo_ok())
        self.assertEqual(len(ai.generate_photo_questions(self.photo)[2]), 5)
        self.assertEqual(len(fake.requests), 2)


def project_files():
    """Every file under the project except the venv and git internals."""
    found = set()
    for root, dirs, files in os.walk(tutor_app.BASE_DIR):
        dirs[:] = [d for d in dirs if d not in (".venv", ".git", "__pycache__")]
        found.update(os.path.join(root, f) for f in files)
    return found


class PhotoRoutes(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        tutor_app.app.config["TESTING"] = True
        self.client = tutor_app.app.test_client()
        self.kid = make_user(self.db, "Kid", username="kid", grade="Grade 7")
        with self.client.session_transaction() as s:
            s["user_id"] = self.kid

    def post_photo(self, data=None, name="hw.jpg", follow=False):
        self.client.get("/practice")
        with self.client.session_transaction() as s:
            token = s["_csrf"]
        return self.client.post("/practice/photo/start", content_type="multipart/form-data",
                                data={"_csrf": token, "photo": (io.BytesIO(data or image_bytes()), name)},
                                follow_redirects=follow)

    def left(self):
        return pr.ai_generations_left(self.db, self.kid, pr.utcnow())

    def test_photo_becomes_a_set_and_is_not_saved_anywhere(self):
        install(self, photo_ok())
        before = project_files()
        resp = self.post_photo()
        set_id = int(resp.headers["Location"].rsplit("/", 1)[1])
        pset = self.db.execute("SELECT * FROM problem_sets WHERE id = ?", (set_id,)).fetchone()
        self.assertEqual((pset["source"], pset["topic"]), ("photo", "Unit rates"))
        topics = {r[0] for r in self.db.execute("SELECT topic FROM problems WHERE set_id = ?", (set_id,))}
        self.assertEqual(topics, {"Ratios & proportions"})           # feeds the dashboard stats
        self.assertEqual(self.left(), AI_DAILY_LIMIT - 1)
        new_files = {f for f in project_files() - before if not f.endswith((".db", ".db-journal"))}
        self.assertEqual(new_files, set())                           # no photo written to disk
        longest = self.db.execute("SELECT MAX(LENGTH(question_text) + LENGTH(COALESCE(explanation, ''))) "
                                  "FROM problems").fetchone()[0]
        self.assertLess(longest, 2000)                               # no image data in the database

    def test_not_math_photo_uses_a_set_and_shows_the_message(self):
        install(self, {"status": "not_math", "topic": "Other math", "concept": "", "questions": []})
        html = self.post_photo(follow=True).get_data(as_text=True)
        self.assertIn("doesn&#39;t look like a math problem", html)
        self.assertEqual(self.left(), AI_DAILY_LIMIT - 1)

    def test_api_failure_does_not_use_a_set(self):
        install(self, api_error(anthropic.InternalServerError, 500))
        html = self.post_photo(follow=True).get_data(as_text=True)
        self.assertIn("had a problem", html)
        self.assertEqual(self.left(), AI_DAILY_LIMIT)

    def test_not_an_image_never_reaches_the_api(self):
        fake = install(self)
        html = self.post_photo(b"just some text", "notes.txt", follow=True).get_data(as_text=True)
        self.assertIn("isn&#39;t a photo we can read", html)
        self.assertEqual(fake.requests, [])

    def test_daily_limit_is_shared_with_topic_practice(self):
        for _ in range(AI_DAILY_LIMIT):
            pr.record_ai_generation(self.db, self.kid, "topic", "Ratios", None, pr.utcnow())
        fake = install(self)
        html = self.post_photo(follow=True).get_data(as_text=True)
        self.assertIn("used all 20 AI question sets", html)
        self.assertEqual(fake.requests, [])

    def test_too_big_upload_is_refused_politely(self):
        fake = install(self)
        big = b"\xff\xd8" + b"0" * (6 * 1024 * 1024)
        html = self.post_photo(big, follow=True).get_data(as_text=True)
        self.assertIn("Photos must be under 5 MB", html)
        self.assertIn("Photo practice", html)                         # landed back on the Practice page
        self.assertEqual(fake.requests, [])


if __name__ == "__main__":
    unittest.main()
