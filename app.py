"""Tutor App — login / signup for students and tutors (Flask + SQLite)."""
import json
import os
import re
import secrets
import sqlite3
from contextlib import closing
from functools import wraps

from dotenv import load_dotenv

# Load secrets like ANTHROPIC_API_KEY from a .env file next to this one (git-ignored).
# This runs before the imports below because config.py reads environment variables.
# Variables already set in the terminal win over the file.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from flask import (Flask, abort, flash, g, redirect, render_template, request,  # noqa: E402
                   send_from_directory, session, url_for)
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

import ai_practice as ai
import figurines as figs
import shop
import practice as pr          # (the route function below is also called practice)
import streaks
from fundamentals import (TIER_TOPICS, TOPIC_BLURBS, TOPIC_ICONS, TOPICS, make_round,
                          tier_for_grade, topic_key)
from config import AI_DAILY_LIMIT, FUNDAMENTALS_POINTS, FUNDAMENTALS_ROUND_SIZE, PRACTICE_TZ, WEAK_TOPIC_MIN_ATTEMPTS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# STUDYBYSARA_DB lets tests and scripts point the app at a different database file.
DATABASE = os.environ.get("STUDYBYSARA_DB") or os.path.join(BASE_DIR, "tutor_app.db")
# Uploaded files live in instance/ (git-ignored), never in static/ or the repo.
AVATAR_DIR = os.path.join(BASE_DIR, "instance", "avatars")
AVATAR_SIZE = 256                      # saved as a 256x256 square
MAX_UPLOAD_MB = 5
Image.MAX_IMAGE_PIXELS = 40_000_000    # refuse "decompression bomb" images
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
ROLES = ("student", "tutor")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
GRADE_LEVELS = (
    ["Kindergarten"]
    + [f"Grade {n}" for n in range(1, 13)]
    + ["College / University", "Adult learner"]
)
# Tutor codes skip look-alike characters (0/O, 1/I/L) so they're easy to read aloud.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6

app = Flask(__name__)
def load_secret_key():
    """The key that signs session cookies. It must stay the same across restarts, or every
    restart logs everyone out and breaks open forms. Order: SECRET_KEY env var, else
    instance/secret_key (created once, git-ignored)."""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(BASE_DIR, "instance", "secret_key")
    try:
        with open(path, encoding="ascii") as f:
            key = f.read().strip()
        if key:
            return key
    except FileNotFoundError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key = secrets.token_hex(32)
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="ascii") as f:
        f.write(key)
    return key


app.config["SECRET_KEY"] = load_secret_key()
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024  # Flask rejects bigger requests


# ---------- Database ----------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def new_tutor_code(db):
    """Return a random tutor code that no other tutor is using."""
    while True:
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
        if db.execute("SELECT 1 FROM users WHERE tutor_code = ?", (code,)).fetchone() is None:
            return code


def normalize_code(raw):
    """Accept codes typed in lowercase or with spaces/dashes, e.g. 'ab3-k9q'."""
    return re.sub(r"[\s-]", "", raw or "").upper()


def init_db():
    # closing() makes sure the connection is closed; `with sqlite3.connect()` alone only commits.
    with closing(sqlite3.connect(DATABASE)) as db, db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,
                email         TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role          TEXT NOT NULL CHECK (role IN ('student', 'tutor')),
                subject       TEXT,
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Add newer columns to databases created before they existed.
        existing = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        new_columns = {
            "grade_level": "TEXT",                              # students only
            "tutor_code": "TEXT",                               # tutors only
            "tutor_id": "INTEGER REFERENCES users(id)",         # student -> their tutor
            "username": "TEXT",                                 # students: public name for leaderboards
            "avatar": "TEXT",                                   # filename in instance/avatars/
        }
        for column, ddl in new_columns.items():
            if column not in existing:
                db.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tutor_code ON users(tutor_code)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_tutor_id ON users(tutor_id)")
        # COLLATE NOCASE: "Sara_M" and "sara_m" count as the same username.
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username COLLATE NOCASE)")
        # Practice tables (problem_sets, problems, attempts, points_ledger): see practice.py.
        db.executescript(pr.SCHEMA)
        db.executescript(shop.SCHEMA)          # figurine purchases
        # Give a code to any tutor who signed up before codes existed.
        for (tutor_id,) in db.execute(
            "SELECT id FROM users WHERE role = 'tutor' AND tutor_code IS NULL"
        ).fetchall():
            db.execute("UPDATE users SET tutor_code = ? WHERE id = ?", (new_tutor_code(db), tutor_id))


def find_tutor_by_code(code):
    return get_db().execute(
        "SELECT * FROM users WHERE role = 'tutor' AND tutor_code = ?", (code,)
    ).fetchone()


# ---------- Usernames ----------
def username_error(username, user_id=None):
    """Return an error message if `username` can't be used, else None.
    `user_id` lets a student keep their own current username."""
    if not USERNAME_RE.match(username):
        return "Usernames are 3–20 characters: letters, numbers and underscores only."
    taken = get_db().execute(
        "SELECT id FROM users WHERE username = ? COLLATE NOCASE AND id IS NOT ?",
        (username, user_id),
    ).fetchone()
    if taken:
        return "That username is taken. Try another."
    return None


# ---------- Profile pictures ----------
def save_avatar(file_storage, user_id):
    """Validate an uploaded image, crop it to a square, re-encode it, and return
    the new filename. Raises ValueError with a friendly message if it's not usable.

    Re-encoding with Pillow (instead of saving the upload as-is) means we only ever
    store a clean image we created: no hidden scripts, and no EXIF metadata such as
    the GPS location phones embed in photos.
    """
    try:
        with Image.open(file_storage.stream) as img:
            if img.format not in ("JPEG", "PNG", "GIF", "WEBP"):
                raise ValueError("Upload a JPG, PNG, GIF or WebP image.")
            img = ImageOps.exif_transpose(img)        # respect phone rotation, then drop EXIF
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
            img = ImageOps.fit(img, (AVATAR_SIZE, AVATAR_SIZE), Image.Resampling.LANCZOS)
            os.makedirs(AVATAR_DIR, exist_ok=True)
            # Random part = new URL on every upload, so browsers don't show a cached old picture.
            filename = f"{user_id}-{secrets.token_hex(8)}.webp"
            img.save(os.path.join(AVATAR_DIR, filename), "WEBP", quality=85)
            return filename
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError):
        raise ValueError("That file isn't an image we can read. Try a JPG or PNG.")


def delete_avatar(filename):
    if filename:
        try:
            os.remove(os.path.join(AVATAR_DIR, os.path.basename(filename)))
        except FileNotFoundError:
            pass


# ---------- CSRF protection ----------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.errorhandler(RequestEntityTooLarge)
def too_large(_exc):
    flash(f"That file is too big. Photos must be under {MAX_UPLOAD_MB} MB.", "error")
    if not session.get("user_id"):
        return redirect(url_for("login"))
    if request.path.startswith("/practice"):
        return redirect(url_for("practice") + "#photo-practice")
    return redirect(url_for("profile_edit"))


@app.before_request
def check_csrf():
    if request.method == "POST":
        token = request.form.get("_csrf", "")
        if not token or not secrets.compare_digest(token, session.get("_csrf", "")):
            # Usually an old tab or an expired session, not an attack: nothing is changed,
            # and the student is sent back with a message instead of an error page.
            flash("That page had expired, so nothing was changed. Please try again.", "error")
            back = request.referrer if (request.referrer or "").startswith(request.host_url) else None
            return redirect(back or url_for("index"))


# ---------- Auth helpers ----------
def current_user():
    """The logged-in user's row, or None. Cached on `g` so it's queried once per request."""
    if "user" not in g:
        uid = session.get("user_id")
        g.user = None if uid is None else get_db().execute(
            "SELECT * FROM users WHERE id = ?", (uid,)
        ).fetchone()
    return g.user


@app.template_filter("hue")
def hue(text):
    """Turn a name into a stable color (0-359) so each default avatar keeps its color."""
    return sum(map(ord, text or "?")) * 37 % 360


@app.template_filter("ago")
def ago(moment):
    """'just now', '5 minutes ago', '3 hours ago', 'yesterday', '4 days ago', or a date."""
    if moment is None:
        return "Never"
    seconds = (pr.utcnow() - moment).total_seconds()
    if seconds < 60:
        return "Just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    days = (streaks.practice_day(pr.utcnow()) - streaks.practice_day(moment)).days
    if days == 0:
        return f"{int(seconds // 3600)} hr ago"
    if days == 1:
        return "Yesterday"
    if days < 7:
        return f"{days} days ago"
    return local_time(moment, "%b %-d")


@app.template_filter("local_time")
def local_time(moment, fmt="%b %-d, %-I:%M %p"):
    """Show a stored UTC time in the app's time zone (Pacific)."""
    if isinstance(moment, str):
        moment = pr.from_db(moment)
    return moment.astimezone(PRACTICE_TZ).strftime(fmt) if moment else ""


# Lets templates ask "is this a Fundamentals topic?" for the "Practice this" button.
app.jinja_env.globals["fundamentals_topic"] = topic_key
app.jinja_env.globals["figurine_svg"] = figs.render


@app.template_filter("nice_date")
def nice_date(iso_day):
    """'2026-12-01' -> 'Dec 1'."""
    from datetime import date
    return date.fromisoformat(iso_day).strftime("%b %-d")
app.jinja_env.globals["rarities"] = figs.RARITIES
app.jinja_env.globals["ai_topic_key"] = lambda name: ai.TOPIC_KEY_BY_NAME.get(name)
SOURCE_LABELS = {"fundamentals": "Fundamentals", "topic": "Topic practice", "photo": "Photo practice"}
app.jinja_env.globals["source_labels"] = SOURCE_LABELS
app.jinja_env.globals["weak_min"] = WEAK_TOPIC_MIN_ATTEMPTS


@app.context_processor
def inject_user():
    """Make `current_user` available in every template (used by the header bar)."""
    return {"current_user": current_user()}


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            flash("Please log in first.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def student_required(view):
    """Like login_required, but only for student accounts (tutors don't practice)."""
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user()["role"] != "student":
            flash("Practice is for student accounts.", "error")
            return redirect(url_for("practice"))
        return view(*args, **kwargs)
    return wrapped


# ---------- Routes ----------
@app.route("/")
def index():
    return redirect(url_for("home") if current_user() else url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    form = {"name": "", "email": "", "role": request.args.get("role", "student"),
            "subject": "", "grade_level": "", "tutor_code": "", "username": ""}
    if request.method == "POST":
        form = {
            "name": request.form.get("name", "").strip(),
            "email": request.form.get("email", "").strip().lower(),
            "role": request.form.get("role", ""),
            "subject": request.form.get("subject", "").strip(),
            "grade_level": request.form.get("grade_level", ""),
            "tutor_code": normalize_code(request.form.get("tutor_code")),
            "username": request.form.get("username", "").strip(),
        }
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")

        errors = []
        tutor = None
        if not form["name"]:
            errors.append("Name is required.")
        if not EMAIL_RE.match(form["email"]):
            errors.append("Enter a valid email address.")
        if form["role"] not in ROLES:
            errors.append("Choose whether you are a student or a tutor.")
        if form["role"] == "tutor" and not form["subject"]:
            errors.append("Tutors must list at least one subject.")
        if form["role"] == "student":
            if form["grade_level"] not in GRADE_LEVELS:
                errors.append("Select your current grade level.")
            problem = username_error(form["username"])
            if problem:
                errors.append(problem)
            if form["tutor_code"]:
                tutor = find_tutor_by_code(form["tutor_code"])
                if tutor is None:
                    errors.append("No tutor found with that code. Check it with your tutor, or leave it blank.")
        if len(password) < 8:
            errors.append("Password must be at least 8 characters.")
        if password != confirm:
            errors.append("Passwords do not match.")

        if not errors:
            db = get_db()
            is_tutor = form["role"] == "tutor"
            try:
                cur = db.execute(
                    """INSERT INTO users
                       (name, email, password_hash, role, subject, grade_level, tutor_code, tutor_id, username)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        form["name"], form["email"], generate_password_hash(password), form["role"],
                        form["subject"] if is_tutor else None,
                        None if is_tutor else form["grade_level"],
                        new_tutor_code(db) if is_tutor else None,
                        tutor["id"] if tutor else None,
                        None if is_tutor else form["username"],
                    ),
                )
                db.commit()
            except sqlite3.IntegrityError:
                errors.append("An account with that email (or username) already exists.")
            else:
                session.clear()
                session["user_id"] = cur.lastrowid
                msg = f"Welcome, {form['name']}! Your {form['role']} account is ready."
                if is_tutor:
                    msg += " Share your tutor code below with your students."
                elif tutor:
                    msg += f" You're linked to your tutor, {tutor['name']}."
                flash(msg, "success")
                return redirect(url_for("home"))

        for e in errors:
            flash(e, "error")
    return render_template("signup.html", form=form, grade_levels=GRADE_LEVELS)


@app.route("/login", methods=["GET", "POST"])
def login():
    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Incorrect email or password.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            return redirect(url_for("home"))
    return render_template("login.html", email=email)


def linked_people(user):
    """Return (students, tutor): a tutor's students, or a student's tutor."""
    db = get_db()
    if user["role"] == "tutor":
        students = db.execute(
            "SELECT id, name, email, grade_level, username, avatar, created_at FROM users "
            "WHERE role = 'student' AND tutor_id = ? ORDER BY name",
            (user["id"],),
        ).fetchall()
        return students, None
    tutor = None
    if user["tutor_id"]:
        tutor = db.execute(
            "SELECT name, email, subject FROM users WHERE id = ?", (user["tutor_id"],)
        ).fetchone()
    return [], tutor


@app.route("/home")
@login_required
def home():
    user = current_user()
    students, tutor = linked_people(user)
    goal = wallet = None
    if user["role"] == "student":
        db = get_db()
        goal, wallet = shop.next_goal(db, user["id"], pr.utcnow()), shop.balance(db, user["id"])
    return render_template("home.html", students=students, tutor=tutor, goal=goal, wallet=wallet)


@app.route("/profile")
@login_required
def profile():
    students, tutor = linked_people(current_user())
    return render_template("profile.html", students=students, tutor=tutor)


@app.route("/profile/edit", methods=["GET", "POST"])
@login_required
def profile_edit():
    """Students set their username and profile picture here.
    (Exercise: extend this to edit name, grade level and subjects too.)"""
    user = current_user()
    if user["role"] != "student":
        flash("Profile pictures and usernames are for student accounts.", "error")
        return redirect(url_for("profile"))

    form = {"username": user["username"] or ""}
    if request.method == "POST":
        db = get_db()
        form["username"] = request.form.get("username", "").strip()
        errors = []

        problem = username_error(form["username"], user_id=user["id"])
        if problem:
            errors.append(problem)

        new_avatar = None
        upload = request.files.get("avatar")
        if not errors and upload and upload.filename:
            try:
                new_avatar = save_avatar(upload, user["id"])
            except ValueError as e:
                errors.append(str(e))

        if errors:
            for e in errors:
                flash(e, "error")
        else:
            avatar = user["avatar"]
            if new_avatar:
                delete_avatar(user["avatar"])
                avatar = new_avatar
            elif request.form.get("remove_avatar"):
                delete_avatar(user["avatar"])
                avatar = None
            try:
                db.execute("UPDATE users SET username = ?, avatar = ? WHERE id = ?",
                           (form["username"], avatar, user["id"]))
                db.commit()
            except sqlite3.IntegrityError:          # someone grabbed the username a moment ago
                delete_avatar(new_avatar)
                flash("That username is taken. Try another.", "error")
            else:
                flash("Profile updated.", "success")
                return redirect(url_for("profile"))
    return render_template("profile_edit.html", form=form)


@app.route("/avatars/<path:filename>")
@login_required
def avatar_file(filename):
    # send_from_directory refuses paths like "../app.py", so users can only fetch avatars.
    return send_from_directory(AVATAR_DIR, filename, max_age=60 * 60 * 24 * 365)


# ---------- Practice ----------
@app.route("/practice")
@login_required
def practice():
    user = current_user()
    if user["role"] != "student":
        return render_template("practice.html", status=None, unfinished=None)
    db, now = get_db(), pr.utcnow()
    unfinished = pr.unfinished_set(db, user["id"], "fundamentals")
    answered = 0
    if unfinished:
        answered = db.execute("SELECT COUNT(*) FROM attempts a JOIN problems p ON p.id = a.problem_id "
                              "WHERE p.set_id = ?", (unfinished["id"],)).fetchone()[0]
    in_progress_ai = db.execute(
        """SELECT s.id, s.topic, s.source, COUNT(p.id) AS total, COUNT(a.id) AS answered
           FROM problem_sets s JOIN problems p ON p.set_id = s.id
           LEFT JOIN attempts a ON a.problem_id = p.id
           WHERE s.user_id = ? AND s.source != 'fundamentals' AND s.completed_at IS NULL
           GROUP BY s.id ORDER BY s.id DESC LIMIT 3""", (user["id"],)).fetchall()
    fits, others, test_prep = ai.topics_for(user["grade_level"])
    return render_template("practice.html", status=pr.practice_status(db, user["id"], now),
                           unfinished=unfinished, answered=answered, round_size=FUNDAMENTALS_ROUND_SIZE,
                           fits=fits, others=others, test_prep=test_prep, in_progress_ai=in_progress_ai,
                           ai_left=pr.ai_generations_left(db, user["id"], now), ai_limit=AI_DAILY_LIMIT,
                           max_upload_mb=MAX_UPLOAD_MB)


@app.route("/practice/topic/start", methods=["POST"])
@student_required
def start_topic():
    """Generate 5 AI questions on a catalog topic (counts toward the daily AI limit)."""
    user, db, now = current_user(), get_db(), pr.utcnow()
    topic = ai.TOPICS_BY_KEY.get(request.form.get("topic", ""))
    if topic is None:
        flash("Pick a topic from the list.", "error")
        return redirect(url_for("practice"))
    if pr.ai_generations_left(db, user["id"], now) <= 0:
        flash(f"You've used all {AI_DAILY_LIMIT} AI question sets for today. Fundamentals is unlimited!", "error")
        return redirect(url_for("practice"))
    try:
        problems = ai.generate_topic_questions(topic["key"], user["grade_level"])
    except ai.AIError as e:
        flash(str(e), "error")
        return redirect(url_for("practice"))
    set_id = pr.create_set(db, user["id"], "topic", problems, pr.utcnow(),
                           topic=topic["name"], grade_level=user["grade_level"])
    pr.record_ai_generation(db, user["id"], "topic", topic["name"], set_id, now)
    return redirect(url_for("practice_set", set_id=set_id))


@app.route("/practice/photo/start", methods=["POST"])
@student_required
def start_photo():
    """Turn a photo of a problem into 5 similar AI questions. The photo is processed in
    memory and never saved: only the generated questions are stored."""
    user, db, now = current_user(), get_db(), pr.utcnow()
    back = redirect(url_for("practice") + "#photo-practice")
    if pr.ai_generations_left(db, user["id"], now) <= 0:
        flash(f"You've used all {AI_DAILY_LIMIT} AI question sets for today. Fundamentals is unlimited!", "error")
        return back
    upload = request.files.get("photo")
    if not upload or not upload.filename:
        flash("Choose a photo first.", "error")
        return back
    try:
        photo = ai.prepare_photo(upload)
    except ai.AIError as e:
        flash(str(e), "error")
        return back
    try:
        topic, concept, problems = ai.generate_photo_questions(photo)
    except ai.PhotoRejected as e:
        # The model did look at the photo (blurry / not math), so it used one of today's AI sets.
        pr.record_ai_generation(db, user["id"], "photo", None, None, now)
        flash(str(e), "error")
        return back
    except ai.AIError as e:                         # our side or the API failed: no set used
        flash(str(e), "error")
        return back
    set_id = pr.create_set(db, user["id"], "photo", problems, pr.utcnow(),
                           topic=concept, grade_level=user["grade_level"])
    pr.record_ai_generation(db, user["id"], "photo", topic, set_id, now)
    return redirect(url_for("practice_set", set_id=set_id))


@app.route("/practice/problem/<int:problem_id>/report", methods=["POST"])
@student_required
def report_problem(problem_id):
    user, db = current_user(), get_db()
    problem = db.execute("SELECT set_id FROM problems WHERE id = ? AND created_for = ?",
                         (problem_id, user["id"])).fetchone()
    if problem is None:
        abort(404)
    if pr.report_problem(db, user["id"], problem_id, request.form.get("reason"),
                         request.form.get("note"), pr.utcnow()):
        flash("Thanks for reporting it. We'll take a look.", "success")
    else:
        flash("That question can't be reported.", "error")
    back = request.form.get("back")
    if back == "feedback":
        return redirect(url_for("practice_set", set_id=problem["set_id"], answered=problem_id))
    return redirect(url_for("practice_set", set_id=problem["set_id"]))


@app.route("/practice/fundamentals/start", methods=["POST"])
@student_required
def start_fundamentals():
    """Start a mixed round, or a focused one when the form sends a topic ("Practice this")."""
    user, db = current_user(), get_db()
    focus = topic_key(request.form.get("topic"))
    set_id = None
    if not focus:
        existing = pr.unfinished_set(db, user["id"], "fundamentals")
        if existing:                                # finish the current mixed round first
            set_id = existing["id"]
    if set_id is None:
        problems = make_round(user["grade_level"], size=FUNDAMENTALS_ROUND_SIZE, focus=focus)
        set_id = pr.create_set(db, user["id"], "fundamentals", problems, pr.utcnow(),
                               topic=TOPICS[focus] if focus else "Fundamentals", grade_level=user["grade_level"])
    if request.form.get("back") == "leaderboard":   # started from "Earn more points!"
        session["leaderboard_sets"] = (session.get("leaderboard_sets", []) + [set_id])[-20:]
    return redirect(url_for("practice_set", set_id=set_id))


@app.route("/practice/set/<int:set_id>")
@student_required
def practice_set(set_id):
    """Shows the next unanswered question, the feedback for one just answered
    (?answered=<problem id>), or the round summary when everything is answered."""
    user, db = current_user(), get_db()
    rows = pr.set_problems(db, user["id"], set_id)
    if not rows:
        abort(404)
    pset = db.execute("SELECT * FROM problem_sets WHERE id = ?", (set_id,)).fetchone()
    status = pr.practice_status(db, user["id"], pr.utcnow())
    total = len(rows)
    reported = pr.reported_ids(db, user["id"], [r["id"] for r in rows])
    from_board = set_id in session.get("leaderboard_sets", [])
    page = {"pset": pset, "reported": reported, "report_reasons": pr.REPORT_REASONS,
            "back_url": url_for("leaderboard") if from_board else url_for("practice"),
            "back_label": "Leaderboard" if from_board else "Practice"}

    answered_id = request.args.get("answered", type=int)
    if answered_id:
        done = next((r for r in rows if r["id"] == answered_id and r["is_correct"] is not None), None)
        if done is None:
            return redirect(url_for("practice_set", set_id=set_id))
        has_next = any(r["is_correct"] is None for r in rows)
        return render_template("practice_question.html", mode="feedback", set_id=set_id, total=total,
                               problem=done, choices=json.loads(done["choices"]) if done["choices"] else None,
                               has_next=has_next, status=status, **page)

    current = next((r for r in rows if r["is_correct"] is None), None)
    if current is None:
        correct = sum(r["is_correct"] for r in rows)
        points = sum(r["points_awarded"] for r in rows)
        return render_template("practice_question.html", mode="summary", set_id=set_id, total=total,
                               rows=rows, correct=correct, points=points, status=status, **page)

    pr.mark_served(db, current["id"], pr.utcnow())
    # public_view() strips the answer key: the browser never sees it before submitting.
    return render_template("practice_question.html", mode="question", set_id=set_id, total=total,
                           problem=pr.public_view(current), status=status, **page)


@app.route("/practice/problem/<int:problem_id>/answer", methods=["POST"])
@student_required
def answer_problem(problem_id):
    user, db = current_user(), get_db()
    problem = db.execute("SELECT set_id FROM problems WHERE id = ? AND created_for = ?",
                         (problem_id, user["id"])).fetchone()
    if problem is None:
        abort(404)
    try:
        result = pr.record_attempt(db, user["id"], problem_id, request.form.get("answer"), pr.utcnow())
    except pr.AnswerError as e:
        flash(str(e), "error")
        return redirect(url_for("practice_set", set_id=problem["set_id"]))
    if result["streak_bonus"]:
        tomorrow = streaks.streak_points(result["streak_day"] + 1)
        flash(f"🔥 Streak secured! +{result['streak_bonus']} points. "
              f"Come back tomorrow for +{tomorrow}.", "success")
    return redirect(url_for("practice_set", set_id=problem["set_id"], answered=problem_id))


@app.route("/leaderboard")
@login_required
def leaderboard():
    """All-time leaderboard (usernames only)."""
    user, db = current_user(), get_db()
    is_student = user["role"] == "student"
    rows, me = pr.leaderboard(db, None, viewer_id=user["id"] if is_student else None)
    return render_template("leaderboard.html", rows=rows, me=me, is_student=is_student)


@app.route("/leaderboard/earn")
@student_required
def earn_points():
    """Quick pick: choose a Fundamentals topic and jump straight into a 10-question round."""
    user = current_user()
    keys = TIER_TOPICS[tier_for_grade(user["grade_level"])]
    topics = [{"key": k, "name": TOPICS[k], "blurb": TOPIC_BLURBS[k], "icon": TOPIC_ICONS[k]} for k in keys]
    return render_template("earn_points.html", topics=topics, round_size=FUNDAMENTALS_ROUND_SIZE,
                           points=FUNDAMENTALS_POINTS)


# ---------- Figurine shop ----------
@app.route("/shop")
@student_required
def shop_page():
    user, db, now = current_user(), get_db(), pr.utcnow()
    items = shop.shop_items(db, user["id"], now)
    limited = [i for i in items if i["fig"]["rarity"] == "limited" and i["status"] in ("available", "upcoming", "sold_out")]
    regular = [i for i in items if i["fig"]["rarity"] != "limited"]
    sections = [(r, [i for i in regular if i["fig"]["rarity"] == r]) for r in ("common", "rare", "epic", "legendary")]
    collectable = [i for i in items if i["fig"]["rarity"] != "limited" or i["owned"] or i["status"] != "retired"]
    return render_template("shop.html", limited=limited, sections=sections,
                           wallet=shop.balance(db, user["id"]), earned=shop.points_earned(db, user["id"]),
                           owned_count=sum(1 for i in items if i["owned"]), total=len(collectable))


@app.route("/shop/buy/<figurine_id>", methods=["POST"])
@student_required
def buy_figurine(figurine_id):
    user, db = current_user(), get_db()
    try:
        shop.buy(db, user["id"], figurine_id, pr.utcnow())
    except shop.ShopError as e:
        flash(str(e), "error")
        return redirect(url_for("shop_page"))
    return redirect(url_for("figurine_page", figurine_id=figurine_id, new=1))


@app.route("/collection")
@student_required
def collection():
    user, db, now = current_user(), get_db(), pr.utcnow()
    items = shop.shop_items(db, user["id"], now)
    # Retired limited editions only show up if you own one.
    items = [i for i in items if i["owned"] or not (i["fig"]["rarity"] == "limited" and i["status"] == "retired")]
    items.sort(key=lambda i: (i["owned"] is None, figs.RARITIES[i["fig"]["rarity"]]["order"], i["fig"]["price"]))
    return render_template("collection.html", items=items, owned_count=sum(1 for i in items if i["owned"]),
                           wallet=shop.balance(db, user["id"]))


@app.route("/collection/<figurine_id>")
@student_required
def figurine_page(figurine_id):
    """One figurine you own, big. ?new=1 shows the "you got it!" celebration after buying."""
    user, db = current_user(), get_db()
    fig = figs.BY_ID.get(figurine_id)
    purchase = shop.owned(db, user["id"]).get(figurine_id) if fig else None
    if purchase is None:
        return redirect(url_for("shop_page"))
    return render_template("figurine.html", fig=fig, purchase=purchase, is_new=request.args.get("new") == "1",
                           wallet=shop.balance(db, user["id"]))


@app.route("/dashboard")
@login_required
def dashboard():
    user, db, now = current_user(), get_db(), pr.utcnow()
    students, tutor = linked_people(user)
    if user["role"] == "tutor":
        rows = [{"student": s, **pr.student_summary(db, s["id"], now)} for s in students]
        return render_template("dashboard.html", user=user, rows=rows)
    return render_template(
        "dashboard.html", user=user, tutor=tutor,
        status=pr.practice_status(db, user["id"], now),
        board_rank=pr.user_rank(db, None, user["id"]) if user["username"] else None,
        topics=pr.topic_accuracy(db, user["id"]),
        attempts=pr.recent_attempts(db, user["id"]),
    )


@app.route("/dashboard/students/<int:student_id>")
@login_required
def student_detail(student_id):
    """A tutor's view of one linked student. Anyone else gets a 404."""
    user, db, now = current_user(), get_db(), pr.utcnow()
    if user["role"] != "tutor":
        abort(404)
    student = db.execute("SELECT id, name, username, avatar, grade_level FROM users "
                         "WHERE id = ? AND role = 'student' AND tutor_id = ?", (student_id, user["id"])).fetchone()
    if student is None:
        abort(404)
    return render_template(
        "student_detail.html", student=student,
        summary=pr.student_summary(db, student_id, now),
        status=pr.practice_status(db, student_id, now),
        topics=pr.topic_accuracy(db, student_id),
        attempts=pr.recent_attempts(db, student_id, limit=25),
    )


@app.route("/link-tutor", methods=["POST"])
@login_required
def link_tutor():
    """Let a student who skipped the code at signup link to a tutor later."""
    user = current_user()
    if user["role"] != "student":
        abort(403)
    tutor = find_tutor_by_code(normalize_code(request.form.get("tutor_code")))
    if tutor is None:
        flash("No tutor found with that code.", "error")
    else:
        db = get_db()
        db.execute("UPDATE users SET tutor_id = ? WHERE id = ?", (tutor["id"], user["id"]))
        db.commit()
        flash(f"You're now linked to {tutor['name']}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))


init_db()

if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
    app.logger.warning("ANTHROPIC_API_KEY is not set, so topic and photo practice will show "
                       "'AI practice isn't set up yet'. Add it to .env (see .env.example).")

if __name__ == "__main__":
    app.run(debug=True, port=5001)  # 5000 is used by AirPlay on macOS
