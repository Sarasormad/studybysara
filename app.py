"""Tutor App — login / signup for students and tutors (Flask + SQLite)."""
import os
import re
import secrets
import sqlite3
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "tutor_app.db")
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
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
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
    with sqlite3.connect(DATABASE) as db:
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
    flash(f"That file is too big. Pictures must be under {MAX_UPLOAD_MB} MB.", "error")
    return redirect(url_for("profile_edit") if session.get("user_id") else url_for("login"))


@app.before_request
def check_csrf():
    if request.method == "POST":
        token = request.form.get("_csrf", "")
        if not token or not secrets.compare_digest(token, session.get("_csrf", "")):
            abort(400, "Invalid form token. Please reload the page and try again.")


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
            "SELECT name, email, grade_level, username, avatar, created_at FROM users "
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
    students, tutor = linked_people(current_user())
    return render_template("home.html", students=students, tutor=tutor)


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


@app.route("/practice")
@login_required
def practice():
    return render_template("practice.html")


@app.route("/dashboard")
@login_required
def dashboard():
    students, tutor = linked_people(current_user())
    return render_template("dashboard.html", user=current_user(), students=students, tutor=tutor)


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

if __name__ == "__main__":
    app.run(debug=True, port=5001)  # 5000 is used by AirPlay on macOS
