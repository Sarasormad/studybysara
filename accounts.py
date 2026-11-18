"""Deleting an account and everything stored about it.

App stores require that people can delete their account from inside the app, and it's the
right thing to do (many users are minors). Deleting removes the person's row and every row
that belongs to them. If a tutor deletes their account, their students stay, just unlinked
(they can join a new tutor with a code); the chats with that tutor are deleted.
"""


def delete_account(db, user_id):
    """Remove a user and all their data. Returns the avatar filename (if any) to delete from disk."""
    user = db.execute("SELECT id, role, avatar FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        return None
    my_problems = "SELECT id FROM problems WHERE created_for = ?"
    statements = [
        # Chats this person is part of (as the student or the tutor), and emails about them.
        ("DELETE FROM messages WHERE student_id = ? OR tutor_id = ?", (user_id, user_id)),
        ("DELETE FROM notifications WHERE user_id = ? OR (kind = 'low_accuracy' AND about_id = ?)", (user_id, user_id)),
        # Practice history.
        (f"DELETE FROM problem_reports WHERE user_id = ? OR problem_id IN ({my_problems})", (user_id, user_id)),
        ("DELETE FROM points_ledger WHERE user_id = ?", (user_id,)),
        ("DELETE FROM attempts WHERE user_id = ?", (user_id,)),
        ("DELETE FROM problems WHERE created_for = ?", (user_id,)),
        ("DELETE FROM problem_sets WHERE user_id = ?", (user_id,)),
        ("DELETE FROM ai_generations WHERE user_id = ?", (user_id,)),
        ("DELETE FROM streak_shields WHERE user_id = ?", (user_id,)),
        ("DELETE FROM daily_quests WHERE user_id = ?", (user_id,)),
        ("DELETE FROM purchases WHERE user_id = ?", (user_id,)),
        # A tutor's students stay, unlinked.
        ("UPDATE users SET tutor_id = NULL WHERE tutor_id = ?", (user_id,)),
        ("DELETE FROM users WHERE id = ?", (user_id,)),
    ]
    with db:                                   # all or nothing
        for sql, params in statements:
            db.execute(sql, params)
    return user["avatar"]
