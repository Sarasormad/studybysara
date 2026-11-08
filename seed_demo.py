"""Fill the leaderboard with demo students so you can see it working.

    python seed_demo.py           # add (or refresh) 8 demo students with points
    python seed_demo.py --remove  # delete the demo students and their points

Demo accounts use @demo.example.test emails and can't log in (no real password).
"""
import random
import sqlite3
import sys
from datetime import timedelta

import app as tutor_app          # importing runs init_db(), so tables exist
import practice as pr
import streaks

DEMO_DOMAIN = "@demo.example.test"
NAMES = ["mathwhiz_42", "fraction_fox", "algebrat", "pi_rate", "decimal_dan",
         "ratio_rae", "sum_one", "quad_queen"]


def remove(db):
    ids = [r[0] for r in db.execute("SELECT id FROM users WHERE email LIKE ?", (f"%{DEMO_DOMAIN}",))]
    for uid in ids:
        db.execute("DELETE FROM points_ledger WHERE user_id = ?", (uid,))
        db.execute("DELETE FROM users WHERE id = ?", (uid,))
    db.commit()
    return len(ids)


def seed(db):
    remove(db)
    rng = random.Random(7)
    now = pr.utcnow()
    week_start = streaks.week_start_utc(now)
    for i, username in enumerate(NAMES):
        cur = db.execute(
            "INSERT INTO users (name, email, password_hash, role, grade_level, username) "
            "VALUES (?, ?, '!', 'student', 'Grade 8', ?)",
            (f"Demo Student {i + 1}", f"demo{i + 1}{DEMO_DOMAIN}", username))
        uid = cur.lastrowid
        # Some points earlier (All Time only) and some this week.
        for when, count in [(week_start - timedelta(days=rng.randint(1, 20)), rng.randint(2, 12)),
                            (week_start + (now - week_start) * rng.random(), rng.randint(0, 8))]:
            for _ in range(count):
                db.execute("INSERT INTO points_ledger (user_id, reason, points, practice_day, created_at) "
                           "VALUES (?, 'fundamentals', 10, ?, ?)",
                           (uid, streaks.practice_day(when).isoformat(), pr.to_db(when)))
    db.commit()
    return len(NAMES)


if __name__ == "__main__":
    db = sqlite3.connect(tutor_app.DATABASE)
    if "--remove" in sys.argv:
        print(f"Removed {remove(db)} demo students.")
    else:
        print(f"Added {seed(db)} demo students with points. Open /leaderboard to see them.")
    db.close()
