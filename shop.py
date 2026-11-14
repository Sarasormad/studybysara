"""The figurine shop: wallets and purchases.

Points work like this:
- Earned points live in points_ledger (Fundamentals answers + streak bonuses). The
  leaderboard ranks students by points EARNED, so buying never lowers your rank.
- Spending is recorded in purchases. Your wallet balance = earned - spent.
"""
from datetime import timezone

import figurines as figs
import streaks

SCHEMA = """
-- One row per figurine a student owns. `serial` is the copy number (No. 1, No. 2, ...)
-- for that figurine across all students.
CREATE TABLE IF NOT EXISTS purchases (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    figurine_id TEXT NOT NULL,
    price       INTEGER NOT NULL,
    serial      INTEGER NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_purchases_once ON purchases(user_id, figurine_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_purchases_serial ON purchases(figurine_id, serial);
"""


class ShopError(ValueError):
    """A purchase that isn't allowed; str(error) is a message for the student."""


def _to_db(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def points_earned(db, user_id):
    return db.execute("SELECT COALESCE(SUM(points), 0) FROM points_ledger WHERE user_id = ?",
                      (user_id,)).fetchone()[0]


def points_spent(db, user_id):
    return db.execute("SELECT COALESCE(SUM(price), 0) FROM purchases WHERE user_id = ?",
                      (user_id,)).fetchone()[0]


def balance(db, user_id):
    return points_earned(db, user_id) - points_spent(db, user_id)


def sold_counts(db):
    return {r[0]: r[1] for r in db.execute("SELECT figurine_id, COUNT(*) FROM purchases GROUP BY figurine_id")}


def owned(db, user_id):
    """{figurine_id: purchase row} for everything the student owns."""
    return {r["figurine_id"]: r for r in db.execute(
        "SELECT * FROM purchases WHERE user_id = ? ORDER BY id", (user_id,))}


def shop_items(db, user_id, now):
    """Every figurine with what the shop page needs to show for this student."""
    today = streaks.practice_day(now)
    sold, mine, wallet = sold_counts(db), owned(db, user_id), balance(db, user_id)
    items = []
    for fig in figs.CATALOG:
        n_sold = sold.get(fig["id"], 0)
        status = figs.availability(fig, today, n_sold)
        purchase = mine.get(fig["id"])
        stock = fig.get("limited", {}).get("stock")
        items.append({
            "fig": fig,
            "status": status,
            "owned": purchase,
            "can_buy": purchase is None and status == "available" and wallet >= fig["price"],
            "short_by": max(0, fig["price"] - wallet),
            "sold": n_sold,
            "stock": stock,
            "left": (stock - n_sold) if stock else None,
            "days_left": figs.days_left(fig, today),
            "starts": fig.get("limited", {}).get("start"),
        })
    return items


def next_goal(db, user_id, now):
    """The cheapest figurine the student can still get: drives "N points to go" on Home."""
    items = [i for i in shop_items(db, user_id, now) if not i["owned"] and i["status"] == "available"]
    if not items:
        return None
    return min(items, key=lambda i: (i["fig"]["price"], i["fig"]["name"]))


def buy(db, user_id, figurine_id, now):
    """Buy one figurine. Returns the new purchase row, or raises ShopError.

    Everything happens in one write-locked transaction (BEGIN IMMEDIATE), so a
    double-click can't spend points twice and a limited edition can't be oversold.
    """
    fig = figs.BY_ID.get(figurine_id)
    if fig is None:
        raise ShopError("That figurine doesn't exist.")
    db.commit()                                   # finish anything pending before locking
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT 1 FROM purchases WHERE user_id = ? AND figurine_id = ?",
                      (user_id, figurine_id)).fetchone():
            raise ShopError(f"You already have {fig['name']}!")
        sold = db.execute("SELECT COUNT(*) FROM purchases WHERE figurine_id = ?", (figurine_id,)).fetchone()[0]
        status = figs.availability(fig, streaks.practice_day(now), sold)
        if status == "upcoming":
            raise ShopError(f"{fig['name']} isn't available yet. It arrives {fig['limited']['start']}.")
        if status == "retired":
            raise ShopError(f"{fig['name']} was a limited edition and is no longer available.")
        if status == "sold_out":
            raise ShopError(f"All {fig['limited']['stock']} copies of {fig['name']} have been claimed!")
        wallet = balance(db, user_id)
        if wallet < fig["price"]:
            short = fig["price"] - wallet
            raise ShopError(f"You need {short} more point{'' if short == 1 else 's'} for {fig['name']}.")
        serial = db.execute("SELECT COALESCE(MAX(serial), 0) + 1 FROM purchases WHERE figurine_id = ?",
                            (figurine_id,)).fetchone()[0]
        cur = db.execute("INSERT INTO purchases (user_id, figurine_id, price, serial, created_at) "
                         "VALUES (?, ?, ?, ?, ?)", (user_id, figurine_id, fig["price"], serial, _to_db(now)))
        db.commit()
    except Exception:
        db.rollback()
        raise
    return db.execute("SELECT * FROM purchases WHERE id = ?", (cur.lastrowid,)).fetchone()
