"""Tutor App — login / signup for students and tutors (Flask + SQLite)."""
import os
import re
import secrets
import sqlite3
from functools import wraps

from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE = os.path.join(BASE_DIR, "tutor_app.db")
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
        }
        for column, ddl in new_columns.items():
            if column not in existing:
                db.execute(f"ALTER TABLE users ADD COLUMN {column} {ddl}")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_tutor_code ON users(tutor_code)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_users_tutor_id ON users(tutor_id)")
        # Give a code to any tutor who signed up before codes existed.
        for (tutor_id,) in db.execute(
            "SELECT id FROM users WHERE role = 'tutor' AND tutor_code IS NULL"
        ).fetchall():
            db.execute("UPDATE users SET tutor_code = ? WHERE id = ?", (new_tutor_code(db), tutor_id))


def find_tutor_by_code(code):
    return get_db().execute(
        "SELECT * FROM users WHERE role = 'tutor' AND tutor_code = ?", (code,)
    ).fetchone()


# ---------- CSRF protection ----------
def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token


@app.before_request
def check_csrf():
    if request.method == "POST":
        token = request.form.get("_csrf", "")
        if not token or not secrets.compare_digest(token, session.get("_csrf", "")):
            abort(400, "Invalid form token. Please reload the page and try again.")


# ---------- Auth helpers ----------
def current_user():
    uid = session.get("user_id")
    if uid is None:
        return None
    return get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()


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
    return redirect(url_for("dashboard") if current_user() else url_for("login"))


@app.route("/signup", methods=["GET", "POST"])
def signup():
    form = {"name": "", "email": "", "role": request.args.get("role", "student"),
            "subject": "", "grade_level": "", "tutor_code": ""}
    if request.method == "POST":
        form = {
            "name": request.form.get("name", "").strip(),
            "email": request.form.get("email", "").strip().lower(),
            "role": request.form.get("role", ""),
            "subject": request.form.get("subject", "").strip(),
            "grade_level": request.form.get("grade_level", ""),
            "tutor_code": normalize_code(request.form.get("tutor_code")),
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
                       (name, email, password_hash, role, subject, grade_level, tutor_code, tutor_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        form["name"], form["email"], generate_password_hash(password), form["role"],
                        form["subject"] if is_tutor else None,
                        None if is_tutor else form["grade_level"],
                        new_tutor_code(db) if is_tutor else None,
                        tutor["id"] if tutor else None,
                    ),
                )
                db.commit()
            except sqlite3.IntegrityError:
                errors.append("An account with that email already exists.")
            else:
                session.clear()
                session["user_id"] = cur.lastrowid
                msg = f"Welcome, {form['name']}! Your {form['role']} account is ready."
                if is_tutor:
                    msg += " Share your tutor code below with your students."
                elif tutor:
                    msg += f" You're linked to your tutor, {tutor['name']}."
                flash(msg, "success")
                return redirect(url_for("dashboard"))

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
            return redirect(url_for("dashboard"))
    return render_template("login.html", email=email)


@app.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    db = get_db()
    students, tutor = [], None
    if user["role"] == "tutor":
        students = db.execute(
            "SELECT name, email, grade_level, created_at FROM users "
            "WHERE role = 'student' AND tutor_id = ? ORDER BY name",
            (user["id"],),
        ).fetchall()
    elif user["tutor_id"]:
        tutor = db.execute(
            "SELECT name, email, subject FROM users WHERE id = ?", (user["tutor_id"],)
        ).fetchone()
    return render_template("dashboard.html", user=user, students=students, tutor=tutor)


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
