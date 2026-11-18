"""Email notifications.

Three kinds:
- help          A student tapped "Ask my tutor": the tutor gets an email right away.
- reminder      A friendly daily nudge to students who haven't finished today's 10 minutes.
                Like Duolingo's, but kind (many students are minors):
                  * at most one a day, only between REMINDER_HOUR and REMINDER_LATEST_HOUR,
                  * never guilt-tripping, and honest about streak shields,
                  * backs off when someone stops practicing, then stops completely,
                  * every email has a one-click unsubscribe, and it's a setting on Edit profile.
- low_accuracy  Tells a tutor when a student's overall accuracy is low (once a week at most
                per student, as one email listing everyone new).

run_due() is called on a schedule (see app.py: `flask --app app notify`, or the dev server's
background loop). Every email sent is logged in `notifications`, which is what stops repeats.
"""
import os
from datetime import timedelta

from flask import current_app, render_template, url_for
from itsdangerous import BadSignature, URLSafeSerializer

import mailer
import practice as pr
import streaks
from config import (ATTENTION_LOW_ACCURACY, ATTENTION_MIN_ANSWERS, LOW_ACCURACY_REPEAT_DAYS,
                    PRACTICE_TZ, REMINDER_DAILY_IDLE_DAYS, REMINDER_HOUR, REMINDER_LATER_DAYS,
                    REMINDER_LATEST_HOUR)

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id),     -- who it was sent to
    kind         TEXT NOT NULL CHECK (kind IN ('help', 'reminder', 'low_accuracy')),
    about_id     INTEGER,                                   -- the student (alerts) or message (help)
    practice_day TEXT NOT NULL,
    sent_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, kind, practice_day);
"""

# Which users-table column turns each kind on or off.
SETTING = {"help": "notify_help", "reminder": "notify_reminders", "low_accuracy": "notify_alerts"}
LABEL = {"help": "help requests from students", "reminder": "daily practice reminders",
         "low_accuracy": "low-accuracy alerts"}


def link(endpoint, **values):
    """A full link for an email, always on the app's public address (APP_BASE_URL)."""
    base = os.environ.get("APP_BASE_URL", "http://localhost:5001").rstrip("/")
    return base + url_for(endpoint, **values)


# ---------- Unsubscribe links ----------
def _signer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="unsubscribe")


def unsubscribe_token(user_id, kind):
    return _signer().dumps({"u": user_id, "k": kind})


def read_token(token):
    """(user_id, kind) from an unsubscribe link, or None if it's been tampered with."""
    try:
        data = _signer().loads(token)
    except BadSignature:
        return None
    return (data.get("u"), data.get("k")) if data.get("k") in SETTING else None


# ---------- Sending ----------
def _log(db, user_id, kind, about_id, now):
    db.execute("INSERT INTO notifications (user_id, kind, about_id, practice_day, sent_at) VALUES (?, ?, ?, ?, ?)",
               (user_id, kind, about_id, streaks.practice_day(now).isoformat(), pr.to_db(now)))
    db.commit()


def _deliver(user, kind, template, subject, **context):
    unsub = link("unsubscribe", token=unsubscribe_token(user["id"], kind))
    context.update(user=user, unsubscribe_url=unsub, what=LABEL[kind])
    text = render_template(f"email/{template}.txt", **context)
    html = render_template(f"email/{template}.html", subject=subject, **context)
    sender = mailer.send if current_app.testing else mailer.send_later
    sender(user["email"], subject, text, html, unsubscribe_url=unsub)


# ---------- Help requests (sent right away) ----------
def help_requested(db, student, message_id, now):
    tutor = db.execute("SELECT * FROM users WHERE id = ?", (student["tutor_id"],)).fetchone()
    if tutor is None or not tutor["notify_help"]:
        return False
    problem = db.execute(
        """SELECT m.body, p.question_text, p.topic FROM messages m JOIN problems p ON p.id = m.problem_id
           WHERE m.id = ?""", (message_id,)).fetchone()
    _deliver(tutor, "help", "help", f"🙋 {student['name']} asked for help on a question",
             student=student, problem=problem,
             chat_url=link("messages_thread", other_id=student["id"]))
    _log(db, tutor["id"], "help", message_id, now)
    return True


# ---------- Daily reminders for students ----------
def reminder_for(db, student, now):
    """What today's reminder should say, or None if this student shouldn't get one now."""
    local_hour = now.astimezone(PRACTICE_TZ).hour
    if not (REMINDER_HOUR <= local_hour < REMINDER_LATEST_HOUR) or not student["notify_reminders"]:
        return None
    today = streaks.practice_day(now)
    if db.execute("SELECT 1 FROM notifications WHERE user_id = ? AND kind = 'reminder' AND practice_day = ?",
                  (student["id"], today.isoformat())).fetchone():
        return None
    status = pr.practice_status(db, student["id"], now)
    if status["secured"]:                                     # today's 10 minutes are done
        return None
    last = pr.student_summary(db, student["id"], now)["last_active"] or pr.from_db(student["created_at"])
    idle = (today - streaks.practice_day(last)).days
    # Every day for the first couple of days, then only now and then, then stop for good.
    if idle > REMINDER_DAILY_IDLE_DAYS and idle not in REMINDER_LATER_DAYS:
        return None
    if status["today_minutes"] > 0:
        kind = "almost"
    elif status["current_streak"] > 0:
        kind = "streak"
    elif idle >= 2:
        kind = "comeback"
    else:
        kind = "daily"
    return {"kind": kind, "status": status, "idle": idle}


REMINDER_SUBJECTS = {
    "almost": "You're {left} minutes from today's goal ✏️",
    "streak": "🔥 Keep your {streak}-day streak going",
    "comeback": "Your math practice misses you 👋",
    "daily": "Time for today's 10 minutes of math ✏️",
}


def send_reminder(db, student, now, reminder):
    s = reminder["status"]
    subject = REMINDER_SUBJECTS[reminder["kind"]].format(
        left=s["goal_minutes"] - s["today_minutes"], streak=s["current_streak"])
    _deliver(student, "reminder", "reminder", subject, r=reminder, s=s,
             practice_url=link("practice"))
    _log(db, student["id"], "reminder", None, now)


# ---------- Low-accuracy alerts for tutors ----------
def low_accuracy_students(db, tutor, now):
    """Students of this tutor whose overall accuracy is low and who haven't been flagged lately."""
    since = pr.to_db(now - timedelta(days=LOW_ACCURACY_REPEAT_DAYS))
    out = []
    for s in db.execute("SELECT id, name, username FROM users WHERE role = 'student' AND tutor_id = ? ORDER BY name",
                        (tutor["id"],)):
        summary = pr.student_summary(db, s["id"], now)
        if summary["answered"] < ATTENTION_MIN_ANSWERS or summary["accuracy"] >= ATTENTION_LOW_ACCURACY:
            continue
        recent = db.execute("SELECT 1 FROM notifications WHERE user_id = ? AND kind = 'low_accuracy' "
                            "AND about_id = ? AND sent_at > ?", (tutor["id"], s["id"], since)).fetchone()
        if not recent:
            out.append({"student": s, **summary})
    return out


def send_low_accuracy(db, tutor, flagged, now):
    names = ", ".join(f["student"]["name"] for f in flagged)
    subject = (f"📉 {names} could use some help" if len(flagged) <= 2
               else f"📉 {len(flagged)} students could use some help")
    _deliver(tutor, "low_accuracy", "low_accuracy", subject, flagged=flagged,
             threshold=ATTENTION_LOW_ACCURACY,
             links={f["student"]["id"]: link("student_detail", student_id=f["student"]["id"])
                    for f in flagged})
    for f in flagged:
        _log(db, tutor["id"], "low_accuracy", f["student"]["id"], now)


# ---------- The scheduled run ----------
def run_due(db, now):
    """Send every reminder and alert that is due right now. Safe to run as often as you like."""
    sent = {"reminder": 0, "low_accuracy": 0}
    for student in db.execute("SELECT * FROM users WHERE role = 'student' AND notify_reminders = 1").fetchall():
        reminder = reminder_for(db, student, now)
        if reminder:
            send_reminder(db, student, now, reminder)
            sent["reminder"] += 1
    if REMINDER_HOUR <= now.astimezone(PRACTICE_TZ).hour < REMINDER_LATEST_HOUR:   # daytime only
        for tutor in db.execute("SELECT * FROM users WHERE role = 'tutor' AND notify_alerts = 1").fetchall():
            flagged = low_accuracy_students(db, tutor, now)
            if flagged:
                send_low_accuracy(db, tutor, flagged, now)
                sent["low_accuracy"] += 1
    return sent
