"""Shared test helpers: a fresh database per test and quick ways to make users and answers."""
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import app as tutor_app
import practice as pr

LA_NOON_UTC_HOUR = 20      # 12:00 PM in Los Angeles (PST) is 20:00 UTC


def fresh_db():
    """A brand-new database with the full schema; also makes the Flask app use it."""
    path = os.path.join(tempfile.mkdtemp(), "test.db")
    tutor_app.DATABASE = path
    tutor_app.init_db()
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def make_user(db, name="Student", role="student", username=None, grade="Grade 7"):
    cur = db.execute(
        "INSERT INTO users (name, email, password_hash, role, grade_level, username) VALUES (?, ?, 'x', ?, ?, ?)",
        (name, f"{name.lower().replace(' ', '')}@example.test", role, grade if role == "student" else None, username))
    db.commit()
    return cur.lastrowid


def noon(day_offset=0, base=datetime(2026, 11, 2, tzinfo=timezone.utc)):
    """Noon Los Angeles time, `day_offset` days after Monday 2026-11-02."""
    return base.replace(hour=LA_NOON_UTC_HOUR) + timedelta(days=day_offset)


def answer(db, user_id, served_at, seconds, source="fundamentals", correct=True,
           topic="Integer arithmetic", question="What is 2 + 2?"):
    """Serve one new question at `served_at` and answer it `seconds` later. Returns record_attempt's result."""
    set_id = pr.create_set(db, user_id, source, [{
        "topic": topic, "question_text": question, "answer_type": "short_answer",
        "choices": None, "correct_answer": "4", "explanation": "2 + 2 = 4."}], served_at)
    problem_id = db.execute("SELECT id FROM problems WHERE set_id = ?", (set_id,)).fetchone()[0]
    pr.mark_served(db, problem_id, served_at)
    return pr.record_attempt(db, user_id, problem_id, "4" if correct else "5",
                             served_at + timedelta(seconds=seconds))


def practice_minutes(db, user_id, start, minutes):
    """Answer enough 3-minute questions to log `minutes` of active time starting at `start`.
    Returns the list of results."""
    results, t, left = [], start, minutes * 60
    while left > 0:
        step = min(left, 180)
        results.append(answer(db, user_id, t, step))
        t += timedelta(seconds=step)
        left -= step
    return results
