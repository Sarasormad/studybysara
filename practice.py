"""Practice data layer: problem sets, attempts, points and streaks.

Every function takes a sqlite3 connection (`db`) and, where time matters, a `now`
(an aware UTC datetime). Passing `now` in, instead of reading the clock inside,
is what lets the tests simulate "11 minutes later" or "two days later".
"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import streaks
from config import (AI_DAILY_LIMIT, BREAK_NUDGE_MINUTES, DAILY_GOAL_SECONDS, MAX_SHIELDS, SHIELD_EVERY_DAYS, FUNDAMENTALS_POINTS, LEADERBOARD_SIZE,
                    RECENT_ATTEMPTS_SHOWN, STREAK_BONUS_CAP, WEAK_TOPIC_MIN_ATTEMPTS,
                    WEAK_TOPICS_SHOWN)
from grading import answers_match

SCHEMA = """
-- A set of questions served together: a 10-question Fundamentals round,
-- or (later phases) 5 AI questions on a topic or from a photo.
CREATE TABLE IF NOT EXISTS problem_sets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    source       TEXT NOT NULL CHECK (source IN ('fundamentals', 'topic', 'photo')),
    topic        TEXT,
    created_at   TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sets_user ON problem_sets(user_id, completed_at);

-- One question. Problems are generated *for* one student (created_for), so the
-- answer key never has to be shared between users.
CREATE TABLE IF NOT EXISTS problems (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id         INTEGER REFERENCES problem_sets(id),
    position       INTEGER,
    source         TEXT NOT NULL CHECK (source IN ('fundamentals', 'topic', 'photo')),
    topic          TEXT,
    grade_level    TEXT,
    question_text  TEXT NOT NULL,
    answer_type    TEXT NOT NULL CHECK (answer_type IN ('multiple_choice', 'short_answer')),
    choices        TEXT,              -- JSON list, multiple choice only
    correct_answer TEXT NOT NULL,
    explanation    TEXT,
    hint           TEXT,              -- format tip for short answers
    created_for    INTEGER NOT NULL REFERENCES users(id),
    served_at      TEXT,              -- last time it was shown (UTC); starts the clock
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_problems_set ON problems(set_id, position);

-- A student's answer. One per problem, so a question can't be re-answered for points.
CREATE TABLE IF NOT EXISTS attempts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id            INTEGER NOT NULL REFERENCES users(id),
    problem_id         INTEGER NOT NULL REFERENCES problems(id),
    submitted_answer   TEXT NOT NULL,
    is_correct         INTEGER NOT NULL,
    points_awarded     INTEGER NOT NULL DEFAULT 0,
    time_spent_seconds INTEGER NOT NULL DEFAULT 0,
    practice_day       TEXT NOT NULL,  -- local date (PRACTICE_TZ) it was answered, e.g. 2026-11-08
    created_at         TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_attempts_once ON attempts(user_id, problem_id);
CREATE INDEX IF NOT EXISTS idx_attempts_day ON attempts(user_id, practice_day);

-- Every point ever earned, with the reason. The leaderboard is SUM(points).
CREATE TABLE IF NOT EXISTS points_ledger (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    reason       TEXT NOT NULL CHECK (reason IN ('fundamentals', 'streak', 'chest')),
    points       INTEGER NOT NULL,
    attempt_id   INTEGER REFERENCES attempts(id),
    practice_day TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_user ON points_ledger(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_ledger_time ON points_ledger(created_at);
-- At most one streak bonus per student per day, enforced by the database.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_streak_once
    ON points_ledger(user_id, practice_day) WHERE reason = 'streak';
-- Streak shields: earned on every 7th real streak day, used up to cover a missed day.
CREATE TABLE IF NOT EXISTS streak_shields (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    earned_day  TEXT NOT NULL,
    used_day    TEXT,                -- the missed day it covered (NULL = still held)
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shields_user ON streak_shields(user_id, used_day);
-- ...and at most one daily-quest chest per student per day.
CREATE UNIQUE INDEX IF NOT EXISTS idx_ledger_chest_once
    ON points_ledger(user_id, practice_day) WHERE reason = 'chest';

-- Each successful AI question set (topic or photo), for the daily limit.
CREATE TABLE IF NOT EXISTS ai_generations (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    kind         TEXT NOT NULL CHECK (kind IN ('topic', 'photo')),
    topic        TEXT,
    set_id       INTEGER REFERENCES problem_sets(id),
    practice_day TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_generations_day ON ai_generations(user_id, practice_day);

-- "Report a problem" on AI questions, since an AI answer key can be wrong.
CREATE TABLE IF NOT EXISTS problem_reports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id INTEGER NOT NULL REFERENCES problems(id),
    user_id    INTEGER NOT NULL REFERENCES users(id),
    reason     TEXT NOT NULL CHECK (reason IN ('wrong_answer', 'confusing', 'other')),
    note       TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_once ON problem_reports(user_id, problem_id);
"""


def migrate(db):
    """One-time upgrades for databases created by older versions. Safe to run every start.

    points_ledger gained the 'chest' reason. SQLite can't change a CHECK rule in place,
    so the table is rebuilt once with every row copied over (same ids)."""
    row = db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'points_ledger'").fetchone()
    if row and "'chest'" not in row[0]:
        db.executescript("""
            BEGIN;
            ALTER TABLE points_ledger RENAME TO points_ledger_old;
            DROP INDEX IF EXISTS idx_ledger_user;
            DROP INDEX IF EXISTS idx_ledger_time;
            DROP INDEX IF EXISTS idx_ledger_streak_once;
            CREATE TABLE points_ledger (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL REFERENCES users(id),
                reason       TEXT NOT NULL CHECK (reason IN ('fundamentals', 'streak', 'chest')),
                points       INTEGER NOT NULL,
                attempt_id   INTEGER REFERENCES attempts(id),
                practice_day TEXT NOT NULL,
                created_at   TEXT NOT NULL
            );
            INSERT INTO points_ledger (id, user_id, reason, points, attempt_id, practice_day, created_at)
                SELECT id, user_id, reason, points, attempt_id, practice_day, created_at FROM points_ledger_old;
            DROP TABLE points_ledger_old;
            COMMIT;
        """)


# ---------- Time helpers (stored as UTC text, like SQLite's CURRENT_TIMESTAMP) ----------
def utcnow():
    return datetime.now(timezone.utc)


def to_db(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def from_db(text):
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if text else None


# ---------- Creating and serving problems ----------
def create_set(db, user_id, source, problems, now, topic=None, grade_level=None):
    """Save a list of problem dicts as one set and return the set id."""
    cur = db.execute("INSERT INTO problem_sets (user_id, source, topic, created_at) VALUES (?, ?, ?, ?)",
                     (user_id, source, topic, to_db(now)))
    set_id = cur.lastrowid
    for position, p in enumerate(problems, start=1):
        db.execute(
            """INSERT INTO problems (set_id, position, source, topic, grade_level, question_text,
                   answer_type, choices, correct_answer, explanation, hint, created_for, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (set_id, position, source, p["topic"], grade_level, p["question_text"], p["answer_type"],
             json.dumps(p["choices"]) if p.get("choices") else None, p["correct_answer"],
             p.get("explanation"), p.get("hint"), user_id, to_db(now)),
        )
    db.commit()
    return set_id


def unfinished_set(db, user_id, source):
    return db.execute(
        "SELECT * FROM problem_sets WHERE user_id = ? AND source = ? AND completed_at IS NULL "
        "ORDER BY id DESC LIMIT 1", (user_id, source)).fetchone()


def set_problems(db, user_id, set_id):
    """All problems in a set with the student's attempt (if any). None if it's not their set."""
    owner = db.execute("SELECT 1 FROM problem_sets WHERE id = ? AND user_id = ?", (set_id, user_id)).fetchone()
    if owner is None:
        return None
    return db.execute(
        """SELECT p.*, a.submitted_answer, a.is_correct, a.points_awarded
           FROM problems p
           LEFT JOIN attempts a ON a.problem_id = p.id AND a.user_id = ?
           WHERE p.set_id = ? ORDER BY p.position""", (user_id, set_id)).fetchall()


def mark_served(db, problem_id, now):
    """Start (or restart) the clock for a question the student is looking at."""
    db.execute("UPDATE problems SET served_at = ? WHERE id = ?", (to_db(now), problem_id))
    db.commit()


def public_view(problem):
    """Only what the browser needs to show a question: never the answer key or explanation."""
    return {
        "id": problem["id"],
        "position": problem["position"],
        "topic": problem["topic"],
        "question_text": problem["question_text"],
        "answer_type": problem["answer_type"],
        "choices": json.loads(problem["choices"]) if problem["choices"] else None,
        "hint": problem["hint"],
    }


# ---------- Answering ----------
class AnswerError(ValueError):
    pass


def record_attempt(db, user_id, problem_id, submitted, now):
    """Grade an answer on the server and record time, points and any streak bonus.

    Returns a dict: is_correct, points, streak_bonus (0 if none), streak_day.
    Raises AnswerError for answers we shouldn't accept.
    """
    problem = db.execute("SELECT * FROM problems WHERE id = ? AND created_for = ?",
                         (problem_id, user_id)).fetchone()
    if problem is None:
        raise AnswerError("That question isn't yours.")
    submitted = (submitted or "").strip()
    if not submitted:
        raise AnswerError("Enter an answer first.")
    if len(submitted) > 100:
        raise AnswerError("That answer is too long.")
    if problem["answer_type"] == "multiple_choice" and submitted not in json.loads(problem["choices"]):
        raise AnswerError("Pick one of the choices.")

    is_correct = answers_match(submitted, problem["correct_answer"])
    points = FUNDAMENTALS_POINTS if is_correct and problem["source"] == "fundamentals" else 0
    seconds = streaks.active_seconds(from_db(problem["served_at"]), now)
    day = streaks.practice_day(now).isoformat()

    try:
        cur = db.execute(
            """INSERT INTO attempts (user_id, problem_id, submitted_answer, is_correct, points_awarded,
                   time_spent_seconds, practice_day, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, problem_id, submitted, int(is_correct), points, seconds, day, to_db(now)))
    except sqlite3.IntegrityError as exc:        # the unique index: already answered
        raise AnswerError("You already answered this question.") from exc
    if points:
        db.execute("INSERT INTO points_ledger (user_id, reason, points, attempt_id, practice_day, created_at) "
                   "VALUES (?, 'fundamentals', ?, ?, ?, ?)", (user_id, points, cur.lastrowid, day, to_db(now)))

    bonus, streak_day, shield_earned = award_streak_if_earned(db, user_id, now)

    # Close the set when its last question is answered.
    if problem["set_id"]:
        left = db.execute(
            "SELECT COUNT(*) FROM problems p LEFT JOIN attempts a ON a.problem_id = p.id "
            "WHERE p.set_id = ? AND a.id IS NULL", (problem["set_id"],)).fetchone()[0]
        if left == 0:
            db.execute("UPDATE problem_sets SET completed_at = ? WHERE id = ?", (to_db(now), problem["set_id"]))
    db.commit()
    return {"is_correct": is_correct, "points": points, "streak_bonus": bonus, "streak_day": streak_day,
            "shield_earned": shield_earned}


# ---------- Streaks ----------
def seconds_on_day(db, user_id, day):
    return db.execute("SELECT COALESCE(SUM(time_spent_seconds), 0) FROM attempts "
                      "WHERE user_id = ? AND practice_day = ?", (user_id, day.isoformat())).fetchone()[0]


def qualifying_days(db, user_id):
    """Dates on which the student reached the daily goal."""
    rows = db.execute("SELECT practice_day FROM attempts WHERE user_id = ? GROUP BY practice_day "
                      "HAVING SUM(time_spent_seconds) >= ?", (user_id, DAILY_GOAL_SECONDS)).fetchall()
    return {datetime.strptime(r[0], "%Y-%m-%d").date() for r in rows}


def shields_held(db, user_id):
    return db.execute("SELECT COUNT(*) FROM streak_shields WHERE user_id = ? AND used_day IS NULL",
                      (user_id,)).fetchone()[0]


def shielded_days(db, user_id):
    """Missed days already covered by a used shield."""
    return {datetime.strptime(r[0], "%Y-%m-%d").date() for r in db.execute(
        "SELECT used_day FROM streak_shields WHERE user_id = ? AND used_day IS NOT NULL", (user_id,))}


def streak_context(db, user_id, today):
    """(qualifying days, shielded days, shields held, days a held shield is protecting right now).

    "Protecting" means: the student missed a day or two, has enough shields to cover them,
    and hasn't secured today yet. The shields are only used up once today is secured."""
    days, used, held = qualifying_days(db, user_id), shielded_days(db, user_id), shields_held(db, user_id)
    pending = set() if today in days else streaks.days_a_shield_would_cover(days | used, today, held)
    return days, used, held, pending


def award_streak_if_earned(db, user_id, now):
    """If today just reached the goal and no bonus was given yet, award it (using shields to
    cover recently missed days, and earning a new shield on every 7th day).
    Returns (points, streak_day, shield_earned)."""
    today = streaks.practice_day(now)
    if seconds_on_day(db, user_id, today) < DAILY_GOAL_SECONDS:
        return 0, 0, False
    if db.execute("SELECT 1 FROM points_ledger WHERE user_id = ? AND reason = 'streak' AND practice_day = ?",
                  (user_id, today.isoformat())).fetchone():
        return 0, 0, False                                     # already awarded today
    days, used, held = qualifying_days(db, user_id), shielded_days(db, user_id), shields_held(db, user_id)
    for missed in sorted(streaks.days_a_shield_would_cover((days - {today}) | used, today, held)):
        db.execute("UPDATE streak_shields SET used_day = ? WHERE id = (SELECT id FROM streak_shields "
                   "WHERE user_id = ? AND used_day IS NULL ORDER BY id LIMIT 1)", (missed.isoformat(), user_id))
        used.add(missed)
    streak_day = streaks.streak_ending(days, today, used)
    points = streaks.streak_points(streak_day)
    cur = db.execute(
        "INSERT OR IGNORE INTO points_ledger (user_id, reason, points, practice_day, created_at) "
        "VALUES (?, 'streak', ?, ?, ?)", (user_id, points, today.isoformat(), to_db(now)))
    if not cur.rowcount:
        return 0, 0, False
    shield_earned = streak_day % SHIELD_EVERY_DAYS == 0 and shields_held(db, user_id) < MAX_SHIELDS
    if shield_earned:
        db.execute("INSERT INTO streak_shields (user_id, earned_day, created_at) VALUES (?, ?, ?)",
                   (user_id, today.isoformat(), to_db(now)))
    return points, streak_day, shield_earned


def practice_status(db, user_id, now):
    """Everything the Practice page and dashboard show about today, streaks and points."""
    today = streaks.practice_day(now)
    days, used, held, protecting = streak_context(db, user_id, today)
    linked = used | protecting                       # missed days a shield covers (or will cover)
    seconds = seconds_on_day(db, user_id, today)
    secured = today in days
    yesterday_streak = streaks.streak_ending(days, today - timedelta(days=1), linked)
    today_bonus = db.execute("SELECT points FROM points_ledger WHERE user_id = ? AND reason = 'streak' "
                             "AND practice_day = ?", (user_id, today.isoformat())).fetchone()
    week_start = to_db(streaks.week_start_utc(now))
    week_points = db.execute("SELECT COALESCE(SUM(points), 0) FROM points_ledger WHERE user_id = ? "
                             "AND created_at >= ?", (user_id, week_start)).fetchone()[0]
    total_points = db.execute("SELECT COALESCE(SUM(points), 0) FROM points_ledger WHERE user_id = ?",
                              (user_id,)).fetchone()[0]
    current = streaks.current_streak(days, today, linked)
    return {
        "today_seconds": seconds,
        "minutes_total": seconds // 60,
        "take_a_break": seconds >= BREAK_NUDGE_MINUTES * 60,
        "break_minutes": BREAK_NUDGE_MINUTES,
        "shields": held,
        "max_shields": MAX_SHIELDS,
        "shield_protecting": bool(protecting),
        # Real streak days still needed for the next shield (None when already holding the max).
        "days_to_shield": (SHIELD_EVERY_DAYS - current % SHIELD_EVERY_DAYS) if held < MAX_SHIELDS else None,
        "today_minutes": min(seconds, DAILY_GOAL_SECONDS) // 60,
        "goal_minutes": DAILY_GOAL_SECONDS // 60,
        "percent": min(100, round(100 * seconds / DAILY_GOAL_SECONDS)),
        "secured": secured,
        "today_bonus": today_bonus[0] if today_bonus else 0,
        # What finishing today would earn, and what tomorrow would earn once today is done.
        "bonus_if_finished": streaks.streak_points(yesterday_streak + 1),
        "tomorrow_bonus": streaks.streak_points(current + 1) if secured else None,
        # What tomorrow pays if today gets (or already is) secured: the "come back" reward.
        "next_day_bonus": streaks.streak_points((current if secured else yesterday_streak + 1) + 1),
        "current_streak": current,
        "best_streak": streaks.best_streak(days, used),
        "week_points": week_points,
        "total_points": total_points,
        "bonus_cap": STREAK_BONUS_CAP,
    }


# ---------- Leaderboard ----------
def leaderboard(db, since, viewer_id=None, limit=LEADERBOARD_SIZE):
    """Top students by points since `since` (UTC datetime, or None for all time).

    Returns (rows, me). Rows contain ONLY rank, username, points and is_me:
    no names, emails or pictures. Students without a username are left out.
    Ties share a rank (1, 2, 2, 4).
    """
    ranked = """
        WITH totals AS (
            SELECT u.id, u.username, SUM(l.points) AS points
            FROM points_ledger l JOIN users u ON u.id = l.user_id
            WHERE u.role = 'student' AND u.username IS NOT NULL AND l.created_at >= ?
            GROUP BY u.id HAVING SUM(l.points) > 0
        )
        SELECT id, username, points, RANK() OVER (ORDER BY points DESC) AS rank FROM totals
    """
    since_text = to_db(since) if since else "0000-00-00 00:00:00"
    top = db.execute(ranked + " ORDER BY points DESC, username COLLATE NOCASE LIMIT ?",
                     (since_text, limit)).fetchall()
    rows = [{"rank": r["rank"], "username": r["username"], "points": r["points"],
             "is_me": r["id"] == viewer_id} for r in top]
    me = None
    if viewer_id is not None and not any(r["is_me"] for r in rows):
        mine = db.execute(f"SELECT * FROM ({ranked}) WHERE id = ?", (since_text, viewer_id)).fetchone()
        if mine:
            me = {"rank": mine["rank"], "username": mine["username"], "points": mine["points"], "is_me": True}
    return rows, me


# ---------- Dashboard stats ----------
def user_rank(db, since, user_id):
    """The student's leaderboard rank since `since` (None = all time), or None if unranked."""
    rows, me = leaderboard(db, since, viewer_id=user_id, limit=LEADERBOARD_SIZE)
    mine = next((r for r in rows if r["is_me"]), me)
    return mine["rank"] if mine else None


def topic_accuracy(db, user_id):
    """Accuracy per topic across every practice type, weakest first.

    Each row: topic, answered, correct, accuracy (0-100), weak (True for the
    WEAK_TOPICS_SHOWN lowest topics with enough answers that aren't perfect).
    """
    rows = db.execute(
        """SELECT p.topic AS topic, COUNT(*) AS answered, SUM(a.is_correct) AS correct
           FROM attempts a JOIN problems p ON p.id = a.problem_id
           WHERE a.user_id = ? AND p.topic IS NOT NULL
           GROUP BY p.topic""", (user_id,)).fetchall()
    stats = [{"topic": r["topic"], "answered": r["answered"], "correct": r["correct"],
              "accuracy": round(100 * r["correct"] / r["answered"]), "weak": False} for r in rows]
    # Weakest first; among equal accuracy, the one with more evidence first.
    stats.sort(key=lambda s: (s["correct"] / s["answered"], -s["answered"], s["topic"]))
    candidates = [s for s in stats if s["answered"] >= WEAK_TOPIC_MIN_ATTEMPTS and s["correct"] < s["answered"]]
    for s in candidates[:WEAK_TOPICS_SHOWN]:
        s["weak"] = True
    return stats


def recent_attempts(db, user_id, limit=RECENT_ATTEMPTS_SHOWN):
    return db.execute(
        """SELECT a.submitted_answer, a.is_correct, a.points_awarded, a.created_at,
                  p.question_text, p.topic, p.source, p.correct_answer
           FROM attempts a JOIN problems p ON p.id = a.problem_id
           WHERE a.user_id = ? ORDER BY a.id DESC LIMIT ?""", (user_id, limit)).fetchall()


def student_summary(db, user_id, now):
    """One row of the tutor's student table."""
    totals = db.execute("SELECT COUNT(*), COALESCE(SUM(is_correct), 0), MAX(created_at) FROM attempts "
                        "WHERE user_id = ?", (user_id,)).fetchone()
    answered, correct, last = totals
    week_answered = db.execute("SELECT COUNT(*) FROM attempts WHERE user_id = ? AND created_at >= ?",
                               (user_id, to_db(streaks.week_start_utc(now)))).fetchone()[0]
    topics = topic_accuracy(db, user_id)
    weakest = next((t for t in topics if t["weak"]), None)
    return {
        "last_active": from_db(last),
        "current_streak": _current_streak(db, user_id, streaks.practice_day(now)),
        "week_answered": week_answered,
        "answered": answered,
        "correct": correct,
        "accuracy": round(100 * correct / answered) if answered else None,
        "weakest_topic": weakest["topic"] if weakest else None,
    }


# ---------- AI limits and reports ----------
REPORT_REASONS = {
    "wrong_answer": "The answer key is wrong",
    "confusing": "The question is confusing or broken",
    "other": "Something else",
}


def ai_generations_left(db, user_id, now):
    used = db.execute("SELECT COUNT(*) FROM ai_generations WHERE user_id = ? AND practice_day = ?",
                      (user_id, streaks.practice_day(now).isoformat())).fetchone()[0]
    return max(0, AI_DAILY_LIMIT - used)


def record_ai_generation(db, user_id, kind, topic, set_id, now):
    db.execute("INSERT INTO ai_generations (user_id, kind, topic, set_id, practice_day, created_at) "
               "VALUES (?, ?, ?, ?, ?, ?)",
               (user_id, kind, topic, set_id, streaks.practice_day(now).isoformat(), to_db(now)))
    db.commit()


def report_problem(db, user_id, problem_id, reason, note, now):
    """Save a report on one of the student's own AI questions. Returns False if not allowed."""
    problem = db.execute("SELECT source FROM problems WHERE id = ? AND created_for = ?",
                         (problem_id, user_id)).fetchone()
    if problem is None or problem["source"] == "fundamentals" or reason not in REPORT_REASONS:
        return False
    db.execute("INSERT OR IGNORE INTO problem_reports (problem_id, user_id, reason, note, created_at) "
               "VALUES (?, ?, ?, ?, ?)", (problem_id, user_id, reason, (note or "").strip()[:500] or None, to_db(now)))
    db.commit()
    return True


def reported_ids(db, user_id, problem_ids):
    if not problem_ids:
        return set()
    marks = ",".join("?" * len(problem_ids))
    return {r[0] for r in db.execute(f"SELECT problem_id FROM problem_reports WHERE user_id = ? "
                                     f"AND problem_id IN ({marks})", (user_id, *problem_ids))}


def week_summary(db, user_id, now):
    """What the student did this week (Monday 12 AM Pacific to now)."""
    week_start = streaks.week_start_utc(now)
    monday = streaks.practice_day(week_start)
    answered, correct, seconds = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(is_correct), 0), COALESCE(SUM(time_spent_seconds), 0) "
        "FROM attempts WHERE user_id = ? AND practice_day >= ?", (user_id, monday.isoformat())).fetchone()
    points = db.execute("SELECT COALESCE(SUM(points), 0) FROM points_ledger WHERE user_id = ? AND created_at >= ?",
                        (user_id, to_db(week_start))).fetchone()[0]
    days = sum(1 for d in qualifying_days(db, user_id) if d >= monday)
    return {"answered": answered, "correct": correct, "points": points,
            "minutes": seconds // 60, "streak_days": days,
            "days_so_far": (streaks.practice_day(now) - monday).days + 1}


def overall_accuracy(db, user_id):
    answered, correct = db.execute("SELECT COUNT(*), COALESCE(SUM(is_correct), 0) FROM attempts WHERE user_id = ?",
                                   (user_id,)).fetchone()
    return {"answered": answered, "correct": correct,
            "percent": round(100 * correct / answered) if answered else None}


def recommendations(topics, count=3):
    """The topics to practice next: lowest accuracy first, among topics with enough answers
    (WEAK_TOPIC_MIN_ATTEMPTS) that aren't already perfect. `topics` is topic_accuracy()."""
    candidates = [t for t in topics if t["answered"] >= WEAK_TOPIC_MIN_ATTEMPTS and t["correct"] < t["answered"]]
    candidates.sort(key=lambda t: (t["correct"] / t["answered"], -t["answered"], t["topic"]))
    return candidates[:count]


def _current_streak(db, user_id, today):
    days, used, _held, protecting = streak_context(db, user_id, today)
    return streaks.current_streak(days, today, used | protecting)


# ---------- Tutor: class overview ----------
def _lost_streak(db, user_id, today):
    """Length of a streak that ended in the last 7 days (0 if none, or if one is running)."""
    days, used, _held, protecting = streak_context(db, user_id, today)
    linked = used | protecting
    if streaks.current_streak(days, today, linked):
        return 0
    recent = [d for d in days if 0 < (today - d).days <= 7]
    return streaks.streak_ending(days, max(recent), linked) if recent else 0


def class_overview(db, tutor_id, now):
    """Everything a tutor's Home and Dashboard show about their class."""
    from config import (ATTENTION_INACTIVE_DAYS, ATTENTION_LOW_ACCURACY, ATTENTION_MIN_ANSWERS,
                        ATTENTION_STREAK_DAYS, CLASS_TOPIC_MIN_ANSWERS)
    today = streaks.practice_day(now)
    students = db.execute("SELECT id, name, email, username, avatar, grade_level FROM users "
                          "WHERE role = 'student' AND tutor_id = ? ORDER BY name", (tutor_id,)).fetchall()
    rows, answered_total, correct_total = [], 0, 0
    for s in students:
        summary = student_summary(db, s["id"], now)
        days_idle = (today - streaks.practice_day(summary["last_active"])).days if summary["last_active"] else None
        reasons = []
        if days_idle is None:
            reasons.append("Hasn't practiced yet")
        elif days_idle >= ATTENTION_INACTIVE_DAYS:
            reasons.append(f"Inactive for {days_idle} days")
        if summary["answered"] >= ATTENTION_MIN_ANSWERS and summary["accuracy"] < ATTENTION_LOW_ACCURACY:
            reasons.append(f"Accuracy is {summary['accuracy']}%")
        lost = _lost_streak(db, s["id"], today)
        if lost >= ATTENTION_STREAK_DAYS:
            reasons.append(f"Lost a {lost}-day streak")
        answered_total += summary["answered"]
        correct_total += summary["correct"]
        rows.append({"student": s, **summary, "days_idle": days_idle, "reasons": reasons})

    weak_topics = []
    if students:
        topic_rows = db.execute(
            f"""SELECT p.topic AS topic, COUNT(*) AS answered, SUM(a.is_correct) AS correct,
                       COUNT(DISTINCT a.user_id) AS students
                FROM attempts a JOIN problems p ON p.id = a.problem_id
                WHERE a.user_id IN ({",".join("?" * len(students))}) AND p.topic IS NOT NULL
                GROUP BY p.topic HAVING COUNT(*) >= ? AND SUM(a.is_correct) < COUNT(*)""",
            (*[s["id"] for s in students], CLASS_TOPIC_MIN_ANSWERS)).fetchall()
        weak_topics = sorted(({"topic": t["topic"], "answered": t["answered"], "students": t["students"],
                               "accuracy": round(100 * t["correct"] / t["answered"])} for t in topic_rows),
                             key=lambda t: (t["accuracy"], -t["answered"]))[:3]
    attention = sorted((r for r in rows if r["reasons"]), key=lambda r: (-len(r["reasons"]), r["student"]["name"]))
    return {
        "rows": rows,
        "count": len(rows),
        "active_week": sum(1 for r in rows if r["week_answered"] > 0),
        "accuracy": round(100 * correct_total / answered_total) if answered_total else None,
        "answered": answered_total,
        "weak_topics": weak_topics,
        "attention": attention,
    }
