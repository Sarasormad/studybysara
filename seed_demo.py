"""Fill the app with demo students and practice history so you can see the
leaderboard and dashboards working.

    python seed_demo.py                              # 8 demo students with ~10 days of practice
    python seed_demo.py --tutor tutor@example.test   # ...and link 4 of them to that tutor
    python seed_demo.py --remove                     # delete all demo students and their data

Demo accounts use @demo.example.test emails and can't log in (no real password).
The history is made with the same functions the app uses (record_attempt etc.),
so points, streaks and stats are all real.
"""
import random
import sqlite3
import sys
from datetime import timedelta

import app as tutor_app          # importing runs init_db(), so tables exist
import practice as pr
from fundamentals import make_round

DEMO_DOMAIN = "@demo.example.test"
STUDENTS = [  # username, grade, skill (chance of a right answer), days practiced (most recent first)
    ("mathwhiz_42", "Grade 8", 0.9, [0, 1, 2, 3, 4, 5]),
    ("fraction_fox", "Grade 6", 0.7, [0, 1, 2, 4, 5]),
    ("algebrat", "Grade 9", 0.8, [1, 2, 3]),
    ("pi_rate", "Grade 10", 0.85, [0, 2, 3, 6, 7, 8, 9]),
    ("decimal_dan", "Grade 5", 0.55, [0, 1]),
    ("ratio_rae", "Grade 7", 0.75, [3, 4, 8, 9]),
    ("sum_one", "Grade 4", 0.65, [0]),
    ("quad_queen", "Grade 11", 0.95, [1, 2, 3, 4, 5, 6, 7, 8, 9]),
]


def remove(db):
    ids = [r[0] for r in db.execute("SELECT id FROM users WHERE email LIKE ?", (f"%{DEMO_DOMAIN}",))]
    for uid in ids:
        db.execute("DELETE FROM points_ledger WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM attempts WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM problems WHERE created_for = ?", (uid,))
        db.execute("DELETE FROM problem_sets WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM users WHERE id = ?", (uid,))
    db.commit()
    return len(ids)


def play_round(db, rng, uid, grade, skill, start, weak_topic):
    """Answer one 10-question round, ~60s per question, mostly right."""
    set_id = pr.create_set(db, uid, "fundamentals", make_round(grade, rng=rng), start, topic="Fundamentals")
    t = start
    for p in pr.set_problems(db, uid, set_id):
        pr.mark_served(db, p["id"], t)
        t += timedelta(seconds=rng.randint(40, 80))
        chance = skill - (0.35 if p["topic"] == weak_topic else 0)
        if rng.random() < chance:
            given = p["correct_answer"]
        elif p["choices"]:
            given = next(c for c in pr.public_view(p)["choices"] if c != p["correct_answer"])
        else:
            given = "0" if p["correct_answer"] != "0" else "1"
        pr.record_attempt(db, uid, p["id"], given, t)
    return t


def seed(db, tutor_email=None):
    remove(db)
    rng = random.Random(7)
    now = pr.utcnow()
    tutor = db.execute("SELECT id FROM users WHERE email = ? AND role = 'tutor'",
                       (tutor_email,)).fetchone() if tutor_email else None
    if tutor_email and tutor is None:
        print(f"No tutor account with email {tutor_email}; demo students won't be linked.")
    for i, (username, grade, skill, days) in enumerate(STUDENTS):
        cur = db.execute(
            "INSERT INTO users (name, email, password_hash, role, grade_level, username, tutor_id) "
            "VALUES (?, ?, '!', 'student', ?, ?, ?)",
            (f"Demo {username.replace('_', ' ').title()}", f"demo{i + 1}{DEMO_DOMAIN}", grade, username,
             tutor[0] if tutor and i < 4 else None))
        uid = cur.lastrowid
        weak_topic = rng.choice(["Fractions", "Decimals", "Percents", "Order of operations", "Integer arithmetic"])
        for days_ago in sorted(days, reverse=True):          # oldest first, so streaks build in order
            t = now - timedelta(days=days_ago, hours=rng.randint(1, 4))
            for _ in range(2):                               # 2 rounds ≈ 20 questions ≈ 10+ minutes
                t = play_round(db, rng, uid, grade, skill, t, weak_topic) + timedelta(minutes=2)
    db.commit()
    return len(STUDENTS)


if __name__ == "__main__":
    db = sqlite3.connect(tutor_app.DATABASE)
    db.row_factory = sqlite3.Row
    if "--remove" in sys.argv:
        print(f"Removed {remove(db)} demo students.")
    else:
        email = sys.argv[sys.argv.index("--tutor") + 1] if "--tutor" in sys.argv else None
        print(f"Added {seed(db, email)} demo students with practice history.")
    db.close()
