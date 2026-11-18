"""One-on-one chat between a student and their linked tutor.

Safety rules (many students are minors):
- A conversation is always one student + the tutor they are linked to. Nobody else can
  read or post in it, and students can never message each other.
- Text only, escaped when shown, links not clickable. Messages can't be edited or deleted,
  so there is always a record of what was said.
- A help request ("Ask my tutor") is a message that points at a problem. The tutor sees
  the question and the answer key; the student's view never shows the answer key.
"""
from datetime import timedelta

import practice as pr
from config import MESSAGE_MAX_LENGTH, MESSAGES_PER_HOUR

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id  INTEGER NOT NULL REFERENCES users(id),
    tutor_id    INTEGER NOT NULL REFERENCES users(id),
    sender_id   INTEGER NOT NULL REFERENCES users(id),
    body        TEXT NOT NULL DEFAULT '',
    problem_id  INTEGER REFERENCES problems(id),     -- set for "Ask my tutor" help requests
    created_at  TEXT NOT NULL,
    read_at     TEXT                                 -- when the other person opened it
);
CREATE INDEX IF NOT EXISTS idx_messages_pair ON messages(student_id, tutor_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_unread ON messages(tutor_id, student_id, read_at);
-- One help request per problem.
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_help_once ON messages(problem_id) WHERE problem_id IS NOT NULL;
"""


class MessageError(ValueError):
    """A message that can't be sent; str(error) is shown to the user."""


def pair_for(db, user, other_id=None):
    """(student_id, tutor_id) for the conversation `user` may open, or None.
    Tutors pass the student's id; students always talk to their own tutor."""
    if user["role"] == "student":
        return (user["id"], user["tutor_id"]) if user["tutor_id"] else None
    row = db.execute("SELECT id FROM users WHERE id = ? AND role = 'student' AND tutor_id = ?",
                     (other_id, user["id"])).fetchone()
    return (row["id"], user["id"]) if row else None


def _check_rate(db, sender_id, now):
    recent = db.execute("SELECT COUNT(*) FROM messages WHERE sender_id = ? AND created_at > ?",
                        (sender_id, pr.to_db(now - timedelta(hours=1)))).fetchone()[0]
    if recent >= MESSAGES_PER_HOUR:
        raise MessageError("You've sent a lot of messages in the last hour. Please wait a bit.")


def send(db, sender, pair, body, now, problem_id=None):
    """Save a message from `sender` in the conversation `pair`. Returns the new id."""
    student_id, tutor_id = pair
    if sender["id"] not in pair:
        raise MessageError("You can't post in that conversation.")
    body = (body or "").strip()
    if not body and problem_id is None:
        raise MessageError("Type a message first.")
    if len(body) > MESSAGE_MAX_LENGTH:
        raise MessageError(f"Messages can be up to {MESSAGE_MAX_LENGTH} characters.")
    _check_rate(db, sender["id"], now)
    cur = db.execute(
        "INSERT INTO messages (student_id, tutor_id, sender_id, body, problem_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (student_id, tutor_id, sender["id"], body, problem_id, pr.to_db(now)))
    db.commit()
    return cur.lastrowid


def ask_for_help(db, student, problem_id, note, now):
    """A student's "Ask my tutor" on one of their own problems."""
    pair = pair_for(db, student)
    if pair is None:
        raise MessageError("You're not linked to a tutor yet.")
    if db.execute("SELECT 1 FROM problems WHERE id = ? AND created_for = ?",
                  (problem_id, student["id"])).fetchone() is None:
        raise MessageError("That question isn't yours.")
    if db.execute("SELECT 1 FROM messages WHERE problem_id = ?", (problem_id,)).fetchone():
        raise MessageError("You already asked your tutor about this question.")
    return send(db, student, pair, note, now, problem_id=problem_id)


def asked_ids(db, student_id, problem_ids):
    """Which of these problems the student already asked about."""
    if not problem_ids:
        return set()
    marks = ",".join("?" * len(problem_ids))
    return {r[0] for r in db.execute(
        f"SELECT problem_id FROM messages WHERE student_id = ? AND problem_id IN ({marks})",
        (student_id, *problem_ids))}


def thread(db, pair, viewer, after_id=0, limit=200):
    """Messages in a conversation, oldest first, with any help-request problem attached.
    The answer key is only included when the viewer is the tutor."""
    student_id, tutor_id = pair
    rows = db.execute(
        """SELECT * FROM (
             SELECT m.*, p.question_text, p.topic, p.choices, p.correct_answer,
                    a.submitted_answer, a.is_correct
             FROM messages m
             LEFT JOIN problems p ON p.id = m.problem_id
             LEFT JOIN attempts a ON a.problem_id = m.problem_id AND a.user_id = m.student_id
             WHERE m.student_id = ? AND m.tutor_id = ? AND m.id > ?
             ORDER BY m.id DESC LIMIT ?) ORDER BY id""",
        (student_id, tutor_id, after_id, limit)).fetchall()
    is_tutor = viewer["id"] == tutor_id
    out = []
    for r in rows:
        m = {"id": r["id"], "body": r["body"], "mine": r["sender_id"] == viewer["id"],
             "created_at": pr.from_db(r["created_at"]), "read": r["read_at"] is not None, "problem": None}
        if r["problem_id"]:
            m["problem"] = {"question_text": r["question_text"], "topic": r["topic"],
                            "answered": r["submitted_answer"] is not None,
                            "submitted_answer": r["submitted_answer"], "is_correct": r["is_correct"],
                            "correct_answer": r["correct_answer"] if is_tutor else None}
        out.append(m)
    return out


def mark_read(db, pair, viewer, now):
    """Mark everything the other person sent in this conversation as read."""
    db.execute("UPDATE messages SET read_at = ? WHERE student_id = ? AND tutor_id = ? "
               "AND sender_id != ? AND read_at IS NULL", (pr.to_db(now), *pair, viewer["id"]))
    db.commit()


def unread_count(db, user):
    """Messages waiting for `user` across all their conversations."""
    column = "tutor_id" if user["role"] == "tutor" else "student_id"
    return db.execute(f"SELECT COUNT(*) FROM messages WHERE {column} = ? AND sender_id != ? AND read_at IS NULL",
                      (user["id"], user["id"])).fetchone()[0]


def conversations(db, user):
    """The chat list: for a tutor, every linked student (latest activity first);
    for a student, just their tutor."""
    if user["role"] == "tutor":
        people = db.execute("SELECT id, name, username, avatar FROM users WHERE role = 'student' AND tutor_id = ?",
                            (user["id"],)).fetchall()
        pairs = [(p, (p["id"], user["id"])) for p in people]
    else:
        tutor = db.execute("SELECT id, name, username, avatar FROM users WHERE id = ?",
                           (user["tutor_id"],)).fetchone() if user["tutor_id"] else None
        pairs = [(tutor, (user["id"], tutor["id"]))] if tutor else []
    out = []
    for person, pair in pairs:
        last = db.execute("SELECT body, problem_id, sender_id, created_at FROM messages "
                          "WHERE student_id = ? AND tutor_id = ? ORDER BY id DESC LIMIT 1", pair).fetchone()
        unread = db.execute("SELECT COUNT(*) FROM messages WHERE student_id = ? AND tutor_id = ? "
                            "AND sender_id != ? AND read_at IS NULL", (*pair, user["id"])).fetchone()[0]
        help_waiting = db.execute("SELECT COUNT(*) FROM messages WHERE student_id = ? AND tutor_id = ? "
                                  "AND problem_id IS NOT NULL AND sender_id != ? AND read_at IS NULL",
                                  (*pair, user["id"])).fetchone()[0]
        preview = None
        if last:
            preview = last["body"] or ("🙋 Asked for help with a question" if last["problem_id"] else "")
            if last["sender_id"] == user["id"]:
                preview = "You: " + preview
        out.append({"person": person, "other_id": person["id"], "preview": preview, "unread": unread,
                    "help_waiting": help_waiting, "last_at": pr.from_db(last["created_at"]) if last else None})
    out.sort(key=lambda c: (c["last_at"] is None, -(c["last_at"].timestamp() if c["last_at"] else 0),
                            c["person"]["name"]))
    return out
