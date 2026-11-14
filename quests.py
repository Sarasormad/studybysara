"""Levels, daily quests and the daily reward chest.

Levels come from lifetime points earned (the same total as the leaderboard).

Each student gets 3 quests per day (Pacific time), picked once and stored so they
don't change during the day:
  1. an "easy" quest (answer 15 questions, or practice 10 minutes)
  2. a "skill" quest (get answers right, get some in a row, or a 3-star round),
     scaled to how the student is doing
  3. a "focus" quest on their weakest Fundamentals topic (or finish a round)
Finishing all 3 unlocks a chest worth a FIXED number of points, shown in advance.
No randomness in rewards: the only surprise is the reveal animation.
"""
import math
import random
from datetime import date

import fundamentals
import practice as pr
import streaks
from config import CHEST_POINTS, DAILY_GOAL_SECONDS, LEVEL_STEP

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_quests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    day        TEXT NOT NULL,        -- Pacific date, e.g. 2026-11-13
    slot       INTEGER NOT NULL,     -- 1, 2, 3
    kind       TEXT NOT NULL,        -- answer | minutes | correct | combo | perfect | topic | round
    target     INTEGER NOT NULL,
    topic      TEXT,                 -- for kind = topic
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_quests_slot ON daily_quests(user_id, day, slot);
"""


# ---------- Levels ----------
def level_floor(level):
    """Lifetime points needed to reach `level` (level 1 starts at 0)."""
    return LEVEL_STEP * level * (level - 1)


def level_info(points):
    level = 1
    while points >= level_floor(level + 1):
        level += 1
    floor, ceiling = level_floor(level), level_floor(level + 1)
    return {"level": level, "points": points, "floor": floor, "next": ceiling,
            "into": points - floor, "span": ceiling - floor, "to_go": ceiling - points,
            "percent": round(100 * (points - floor) / (ceiling - floor))}


def user_level(db, user_id):
    earned = db.execute("SELECT COALESCE(SUM(points), 0) FROM points_ledger WHERE user_id = ?",
                        (user_id,)).fetchone()[0]
    return level_info(earned)


# ---------- Picking today's quests ----------
QUEST_ICONS = {"answer": "✏️", "minutes": "⏱️", "correct": "✅", "combo": "🔥", "perfect": "⭐",
               "topic": "🎯", "round": "🏁"}


def _pick(db, user_id, day):
    """Choose 3 quests for a student, the same way every time for the same day."""
    rng = random.Random(f"{user_id}:{day}")
    overall = pr.overall_accuracy(db, user_id)["percent"]
    strong = overall is not None and overall >= 75

    easy = rng.choice([("answer", 15, None, "Answer 15 questions"),
                       ("minutes", DAILY_GOAL_SECONDS // 60, None, f"Practice for {DAILY_GOAL_SECONDS // 60} minutes")])
    skills = [("correct", 10 if strong else 6, None, f"Get {10 if strong else 6} answers right"),
              ("combo", 5 if strong else 3, None, f"Get {5 if strong else 3} right in a row")]
    if strong:
        skills.append(("perfect", 1, None, "Get 3 stars on a Fundamentals round"))
    skill = rng.choice(skills)

    weak = [t for t in pr.recommendations(pr.topic_accuracy(db, user_id))
            if fundamentals.topic_key(t["topic"])]               # Fundamentals topics are unlimited
    if weak:
        topic = weak[0]["topic"]
        focus = ("topic", 5, topic, f"Answer 5 {topic} questions")
    else:
        focus = ("round", 1, None, "Finish a Fundamentals round")
    return [easy, skill, focus]


def todays_quests(db, user_id, now):
    """Today's 3 quests (created on first visit of the day) with live progress."""
    day = streaks.practice_day(now).isoformat()
    rows = db.execute("SELECT * FROM daily_quests WHERE user_id = ? AND day = ? ORDER BY slot",
                      (user_id, day)).fetchall()
    if not rows:
        for slot, (kind, target, topic, title) in enumerate(_pick(db, user_id, day), start=1):
            db.execute("INSERT OR IGNORE INTO daily_quests (user_id, day, slot, kind, target, topic, title, created_at) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (user_id, day, slot, kind, target, topic, title, pr.to_db(now)))
        db.commit()
        rows = db.execute("SELECT * FROM daily_quests WHERE user_id = ? AND day = ? ORDER BY slot",
                          (user_id, day)).fetchall()
    stats = _day_stats(db, user_id, day)
    quests = []
    for q in rows:
        value = _progress(q, stats, db, user_id, day)
        quests.append({"slot": q["slot"], "kind": q["kind"], "title": q["title"], "topic": q["topic"],
                       "target": q["target"], "progress": min(value, q["target"]), "done": value >= q["target"],
                       "icon": QUEST_ICONS.get(q["kind"], "⭐"),
                       "percent": round(100 * min(value, q["target"]) / q["target"])})
    return quests


# ---------- Measuring progress ----------
def _day_stats(db, user_id, day):
    attempts = db.execute("SELECT is_correct FROM attempts WHERE user_id = ? AND practice_day = ? ORDER BY id",
                          (user_id, day)).fetchall()
    best = run = 0
    for a in attempts:
        run = run + 1 if a["is_correct"] else 0
        best = max(best, run)
    return {"answered": len(attempts), "correct": sum(a["is_correct"] for a in attempts), "best_combo": best}


def _rounds_finished_today(db, user_id, day):
    """(finished, three_star) Fundamentals rounds completed on `day` (Pacific)."""
    rows = db.execute(
        """SELECT s.completed_at, COUNT(a.id) AS total, COALESCE(SUM(a.is_correct), 0) AS correct
           FROM problem_sets s JOIN problems p ON p.set_id = s.id JOIN attempts a ON a.problem_id = p.id
           WHERE s.user_id = ? AND s.source = 'fundamentals' AND s.completed_at IS NOT NULL
             AND s.completed_at >= datetime(?, '-1 day')
           GROUP BY s.id""", (user_id, day)).fetchall()
    today = [r for r in rows if streaks.practice_day(pr.from_db(r["completed_at"])).isoformat() == day]
    three_star = [r for r in today if r["correct"] >= math.ceil(0.9 * r["total"])]
    return len(today), len(three_star)


def _progress(q, stats, db, user_id, day):
    kind = q["kind"]
    if kind == "answer":
        return stats["answered"]
    if kind == "correct":
        return stats["correct"]
    if kind == "combo":
        return stats["best_combo"]
    if kind == "minutes":
        return pr.seconds_on_day(db, user_id, date.fromisoformat(day)) // 60
    if kind == "topic":
        return db.execute("SELECT COUNT(*) FROM attempts a JOIN problems p ON p.id = a.problem_id "
                          "WHERE a.user_id = ? AND a.practice_day = ? AND p.topic = ?",
                          (user_id, day, q["topic"])).fetchone()[0]
    if kind in ("round", "perfect"):
        finished, three_star = _rounds_finished_today(db, user_id, day)
        return finished if kind == "round" else three_star
    return 0


# ---------- The chest ----------
def chest_state(db, user_id, now, quests=None):
    """'locked' (quests left), 'ready' (claim it!) or 'opened' (claimed today)."""
    day = streaks.practice_day(now).isoformat()
    opened = db.execute("SELECT 1 FROM points_ledger WHERE user_id = ? AND reason = 'chest' AND practice_day = ?",
                        (user_id, day)).fetchone()
    if opened:
        return "opened"
    quests = quests if quests is not None else todays_quests(db, user_id, now)
    return "ready" if all(q["done"] for q in quests) else "locked"


def open_chest(db, user_id, now):
    """Claim today's chest. Returns the points awarded, or 0 if it isn't ready (or already opened)."""
    if chest_state(db, user_id, now) != "ready":
        return 0
    cur = db.execute("INSERT OR IGNORE INTO points_ledger (user_id, reason, points, practice_day, created_at) "
                     "VALUES (?, 'chest', ?, ?, ?)",
                     (user_id, CHEST_POINTS, streaks.practice_day(now).isoformat(), pr.to_db(now)))
    db.commit()
    return CHEST_POINTS if cur.rowcount else 0


def snapshot(db, user_id, now):
    """Quest completion + level, taken before and after an answer to spot what just happened."""
    quests = todays_quests(db, user_id, now)
    return {"done": {q["slot"] for q in quests if q["done"]}, "quests": quests,
            "level": user_level(db, user_id)["level"]}


def what_changed(before, after):
    """Celebrations to show: newly finished quests, all-done (chest ready), and level-ups."""
    finished = [q for q in after["quests"] if q["done"] and q["slot"] not in before["done"]]
    return {
        "quests_done": [{"title": q["title"], "icon": q["icon"]} for q in finished],
        "chest_ready": bool(finished) and all(q["done"] for q in after["quests"]),
        "level_up": after["level"] if after["level"] > before["level"] else None,
    }

