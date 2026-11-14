import unittest
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

import app as tutor_app
import figurines as figs
import practice as pr
import shop
import streaks
from tests.helpers import fresh_db, make_user

# Pacific noon on a few days around the limited-edition windows in the catalog.
NOV_13 = datetime(2026, 11, 13, 20, tzinfo=timezone.utc)
NOV_26 = datetime(2026, 11, 26, 20, tzinfo=timezone.utc)
DEC_05 = datetime(2026, 12, 5, 20, tzinfo=timezone.utc)
OCT_20 = datetime(2026, 10, 20, 19, tzinfo=timezone.utc)


def give_points(db, user_id, points, when=NOV_13):
    db.execute("INSERT INTO points_ledger (user_id, reason, points, practice_day, created_at) "
               "VALUES (?, 'fundamentals', ?, ?, ?)",
               (user_id, points, streaks.practice_day(when).isoformat(), pr.to_db(when)))
    db.commit()


class Catalog(unittest.TestCase):
    def test_every_figurine_is_valid_and_draws(self):
        ids = [f["id"] for f in figs.CATALOG]
        self.assertEqual(len(ids), len(set(ids)), "figurine ids must be unique")
        for fig in figs.CATALOG:
            with self.subTest(figurine=fig["id"]):
                self.assertIn(fig["rarity"], figs.RARITIES)
                self.assertIn(fig["shape"], figs.SHAPES)
                self.assertIn(fig["eyes"], figs.EYES)
                self.assertIn(fig["mouth"], figs.MOUTHS)
                self.assertTrue(set(fig["accessories"]) <= figs.ACCESSORIES)
                self.assertGreater(fig["price"], 0)
                self.assertEqual(fig["rarity"] == "limited", "limited" in fig)
                if "limited" in fig:
                    start, end = figs.limited_window(fig)
                    self.assertLess(start, end)
                    self.assertGreater(fig["limited"]["stock"], 0)
                svg = str(figs.render(fig, 120, serial=3))
                ET.fromstring(svg)                                   # valid SVG/XML
                self.assertIn(fig["name"], svg)                      # accessible label

    def test_each_render_has_its_own_gradient_ids(self):
        a, b = str(figs.render(figs.BY_ID["dot"])), str(figs.render(figs.BY_ID["dot"]))
        self.assertNotEqual(a.split('id="')[1][:8], b.split('id="')[1][:8])

    def test_availability_windows(self):
        pumpkin = figs.BY_ID["pumpkin-pi"]                           # Nov 1 - Dec 1, 100 copies
        self.assertEqual(figs.availability(pumpkin, date(2026, 10, 31), 0), "upcoming")
        self.assertEqual(figs.availability(pumpkin, date(2026, 11, 1), 0), "available")
        self.assertEqual(figs.availability(pumpkin, date(2026, 11, 30), 99), "available")
        self.assertEqual(figs.availability(pumpkin, date(2026, 11, 30), 100), "sold_out")
        self.assertEqual(figs.availability(pumpkin, date(2026, 12, 1), 0), "retired")
        self.assertEqual(figs.availability(figs.BY_ID["dot"], date(2030, 1, 1), 10**6), "available")


class Buying(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        self.kid = make_user(self.db, "Kid", username="kid")

    def test_buying_spends_points_but_never_lowers_the_leaderboard(self):
        give_points(self.db, self.kid, 500)
        shop.buy(self.db, self.kid, "dot", NOV_13)                   # 150
        self.assertEqual(shop.balance(self.db, self.kid), 350)
        self.assertEqual(shop.points_earned(self.db, self.kid), 500)
        rows, _ = pr.leaderboard(self.db, None)
        self.assertEqual(rows[0]["points"], 500)

    def test_cannot_overspend(self):
        give_points(self.db, self.kid, 149)
        with self.assertRaises(shop.ShopError) as err:
            shop.buy(self.db, self.kid, "dot", NOV_13)
        self.assertIn("1 more point for", str(err.exception))
        self.assertEqual(shop.points_spent(self.db, self.kid), 0)

    def test_one_copy_each(self):
        give_points(self.db, self.kid, 1000)
        shop.buy(self.db, self.kid, "dot", NOV_13)
        with self.assertRaises(shop.ShopError):
            shop.buy(self.db, self.kid, "dot", NOV_13)
        self.assertEqual(shop.balance(self.db, self.kid), 850)       # charged once

    def test_serial_numbers_count_up_across_students(self):
        serials = []
        for i in range(3):
            uid = make_user(self.db, f"S{i}", username=f"s{i}")
            give_points(self.db, uid, 200)
            serials.append(shop.buy(self.db, uid, "zero", NOV_13)["serial"])
        self.assertEqual(serials, [1, 2, 3])

    def test_limited_editions_respect_their_dates(self):
        give_points(self.db, self.kid, 10_000)
        for fig_id, when, words in [("pumpkin-pi", OCT_20, "isn't available yet"),
                                    ("galaxy-graph", NOV_26, "no longer available"),
                                    ("frosty-fraction", NOV_13, "isn't available yet")]:
            with self.subTest(fig=fig_id), self.assertRaises(shop.ShopError) as err:
                shop.buy(self.db, self.kid, fig_id, when)
            self.assertIn(words, str(err.exception))
        self.assertEqual(shop.buy(self.db, self.kid, "frosty-fraction", DEC_05)["serial"], 1)

    def test_limited_stock_runs_out(self):
        stock = figs.BY_ID["galaxy-graph"]["limited"]["stock"]
        for i in range(stock):
            uid = make_user(self.db, f"Fan{i}", username=f"fan{i}")
            give_points(self.db, uid, 3000)
            shop.buy(self.db, uid, "galaxy-graph", NOV_13)
        give_points(self.db, self.kid, 3000)
        with self.assertRaises(shop.ShopError) as err:
            shop.buy(self.db, self.kid, "galaxy-graph", NOV_13)
        self.assertIn(f"All {stock} copies", str(err.exception))
        self.assertEqual(shop.balance(self.db, self.kid), 3000)

    def test_unknown_figurine(self):
        with self.assertRaises(shop.ShopError):
            shop.buy(self.db, self.kid, "not-a-figurine", NOV_13)

    def test_next_goal_is_the_cheapest_figurine_you_dont_own(self):
        give_points(self.db, self.kid, 100)
        goal = shop.next_goal(self.db, self.kid, NOV_13)
        self.assertEqual(goal["fig"]["price"], 150)
        self.assertEqual(goal["short_by"], 50)
        self.assertFalse(goal["can_buy"])


class ShopRoutes(unittest.TestCase):
    def setUp(self):
        self.db = fresh_db()
        self.addCleanup(self.db.close)
        tutor_app.app.config["TESTING"] = True
        self.client = tutor_app.app.test_client()
        self.kid = make_user(self.db, "Kid", username="kid")
        give_points(self.db, self.kid, 400, when=pr.utcnow())
        self.login(self.kid)

    def login(self, user_id):
        with self.client.session_transaction() as s:
            s["user_id"] = user_id

    def token(self):
        self.client.get("/shop")
        with self.client.session_transaction() as s:
            return s["_csrf"]

    def test_shop_shows_wallet_and_buy_flow_reveals_the_figurine(self):
        html = self.client.get("/shop").get_data(as_text=True)
        self.assertIn("400", html)
        self.assertIn("Figurine Shop", html)
        resp = self.client.post("/shop/buy/dot", data={"_csrf": self.token()}, follow_redirects=True)
        page = resp.get_data(as_text=True)
        self.assertIn("You got Dot!", page)
        self.assertIn("No. 1", page)
        self.assertIn("250 points left", page)

    def test_buy_needs_csrf_and_post(self):
        self.client.post("/shop/buy/dot", data={})
        self.assertEqual(shop.points_spent(self.db, self.kid), 0)
        self.assertEqual(self.client.get("/shop/buy/dot").status_code, 405)

    def test_cannot_view_a_figurine_you_dont_own(self):
        resp = self.client.get("/collection/queen-quadratica")
        self.assertEqual(resp.status_code, 302)

    def test_shop_is_for_students_only(self):
        self.login(make_user(self.db, "Tutor", role="tutor"))
        self.assertEqual(self.client.get("/shop").status_code, 302)
        self.assertNotIn(">Shop<", self.client.get("/home").get_data(as_text=True))

    def test_collection_shows_progress(self):
        self.client.post("/shop/buy/dot", data={"_csrf": self.token()})
        html = self.client.get("/collection").get_data(as_text=True)
        self.assertIn("1 of", html)
        self.assertIn("Owned · No. 1", html)


if __name__ == "__main__":
    unittest.main()
