import io
import json
import random
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from trade_bot import app, cli, rolimons, tradeads
from trade_bot.engine import (
    DOWNGRADE, SIDEGRADE, UPGRADE, ComboFinder, TradeRules, generate_trades, match_trade_ads,
)
from trade_bot.models import ItemInfo, OwnedItem
from trade_bot.scanner import build_inventory

# Shape of api.rolimons.com/items/v2/itemdetails:
# [name, acronym, rap, value, default_value, demand, trend, projected, hyped, rare]
ITEM_DETAILS = {
    "success": True,
    "item_count": 9,
    "items": {
        "1": ["Big Hat", "BH", 95000, 100000, 100000, 3, 2, -1, -1, -1],
        "2": ["Medium Hat", "", 48000, 50000, 50000, 2, 2, -1, -1, -1],
        "3": ["Small Hat", "SH", 26000, 25000, 25000, 2, 3, -1, -1, -1],
        "4": ["Tiny Hat", "", 11000, 10000, 10000, 1, 2, -1, -1, -1],
        "5": ["Rap Hat", "", 30000, -1, 30000, -1, -1, -1, -1, -1],
        "6": ["Projected Hat", "", 90000, -1, 90000, -1, -1, 1, -1, -1],
        "7": ["Hyped Hat", "", 60000, 60000, 60000, 4, 3, -1, 1, -1],
        "8": ["Dud Hat", "", 40000, 40000, 40000, 0, 0, -1, -1, -1],
        "9": ["Crown", "", 190000, 200000, 200000, 4, 3, -1, -1, 1],
    },
}


def catalog():
    return rolimons.parse_item_details(ITEM_DETAILS)


def owned(cat, *asset_ids, hold=()):
    return [OwnedItem(cat[a], uaid=n, on_hold=a in hold) for n, a in enumerate(asset_ids)]


class ParseTests(unittest.TestCase):
    def test_parse_item_details(self):
        cat = catalog()
        self.assertEqual(cat[1].trade_value, 100000)
        self.assertEqual(cat[1].label, "BH")
        self.assertEqual(cat[5].trade_value, 30000)  # RAP fallback
        self.assertFalse(cat[5].has_value)
        self.assertTrue(cat[6].projected)
        self.assertTrue(cat[9].rare)

    def test_build_inventory_skips_unknown_and_marks_holds(self):
        cat = catalog()
        inv = build_inventory(1, "me", {1: [100, 101], 999: [5]}, {101}, cat)
        self.assertEqual(len(inv.items), 2)
        self.assertEqual(sum(o.on_hold for o in inv.items), 1)
        self.assertEqual(inv.total_value, 200000)


class ComboFinderTests(unittest.TestCase):
    def test_no_duplicate_multisets(self):
        cat = catalog()
        finder = ComboFinder([cat[3], cat[3], cat[3], cat[4]])
        combos = finder.find(0, 10**9, 2, 3, True, 100, 10_000)
        keys = [tuple(sorted(i.asset_id for i in c)) for c in combos]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn((3, 3, 3), keys)
        self.assertIn((3, 3, 4), keys)

    def test_respects_window(self):
        cat = catalog()
        finder = ComboFinder([cat[1], cat[2], cat[3], cat[4]])
        for c in finder.find(60000, 80000, 1, 4, True, 50, 10_000):
            self.assertTrue(60000 <= sum(i.trade_value for i in c) <= 80000)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.cat = catalog()

    def test_upgrade_found(self):
        mine = owned(self.cat, 2, 3, 3, 4)
        results = generate_trades(mine, [self.cat[1]])
        up = results[UPGRADE]
        self.assertEqual(len(up), 1)
        self.assertEqual(up[0].receive[0].asset_id, 1)
        # 100k for 100k is too even for anyone to accept; 110k is the cheapest realistic offer.
        self.assertEqual(up[0].give_value, 110000)

    def test_upgrade_target_worth_more_than_each_given_item(self):
        mine = owned(self.cat, 1, 4)
        results = generate_trades(mine, [self.cat[2]])
        self.assertEqual(results[UPGRADE], [])

    def test_downgrade_found(self):
        mine = owned(self.cat, 1)
        theirs = [self.cat[2], self.cat[3], self.cat[3], self.cat[4]]
        dn = generate_trades(mine, theirs)[DOWNGRADE]
        self.assertEqual(len(dn), 1)
        t = dn[0]
        self.assertTrue(1.05 <= t.ratio <= 1.20)
        self.assertEqual(t.receive_value, 110000)  # best-for-me total inside the window

    def test_sidegrade_prefers_better_item(self):
        mine = owned(self.cat, 2)
        theirs = [self.cat[2], self.cat[8], self.cat[7]]
        rules = TradeRules(sidegrade_ratio=(0.7, 1.3), allow_hyped=False)
        side = generate_trades(mine, theirs, rules)[SIDEGRADE]
        received = [t.receive[0].asset_id for t in side]
        self.assertNotIn(2, received)  # same item isn't a trade
        self.assertNotIn(7, received)  # hyped excluded by default
        self.assertEqual(received, [8])

    def test_holds_keep_and_projected_never_given(self):
        mine = owned(self.cat, 2, 3, 3, 6, hold=(3,))
        rules = TradeRules(keep={2})
        results = generate_trades(mine, [self.cat[1], self.cat[9], self.cat[4], self.cat[5]], rules)
        for trades in results.values():
            for t in trades:
                self.assertFalse({i.asset_id for i in t.give} & {2, 3, 6})

    def test_min_demand_filters_receive(self):
        mine = owned(self.cat, 1)
        theirs = [self.cat[8], self.cat[5], self.cat[3], self.cat[4], self.cat[4]]
        rules = TradeRules(min_receive_demand=1)
        for trades in generate_trades(mine, theirs, rules).values():
            for t in trades:
                self.assertTrue(all(i.demand >= 1 for i in t.receive))

    def test_large_market_is_fast(self):
        rng = random.Random(1)
        market = [
            ItemInfo(i, f"Item {i}", "", v, v, rng.randint(-1, 4), rng.randint(-1, 4), False, False, False)
            for i, v in enumerate((int(rng.lognormvariate(9, 1.5)) + 1 for _ in range(2500)), start=1)
        ]
        mine = [OwnedItem(rng.choice(market)) for _ in range(120)]
        start = time.time()
        results = generate_trades(mine, market, TradeRules(min_receive_demand=2))
        self.assertLess(time.time() - start, 60)
        self.assertTrue(any(results.values()))
        for kind, (lo, hi) in ((UPGRADE, (0.8, 0.95)), (DOWNGRADE, (1.05, 1.2)), (SIDEGRADE, (0.97, 1.08))):
            for t in results[kind]:
                self.assertTrue(lo <= t.ratio <= hi, (kind, t.ratio))


def ad(ad_id, user_id, offer, request_items=(), tags=(), robux=0):
    return [ad_id, 1700000000, user_id, f"poster{user_id}",
            {"items": list(offer), "robux": robux}, {"items": list(request_items), "tags": list(tags)}]


class TradeAdTests(unittest.TestCase):
    def setUp(self):
        self.cat = catalog()

    def parse(self, *ads):
        return tradeads.parse_trade_ads({"success": True, "trade_ads": list(ads)}, self.cat)

    def test_parse_skips_unknown_items_and_bad_rows(self):
        ads = self.parse(ad(1, 10, [1], [2]), ad(2, 11, [12345]), ["junk"], ad(3, 12, [4], robux=500))
        self.assertEqual([a.ad_id for a in ads], [1, 3])
        self.assertEqual(ads[1].offer_robux, 500)
        self.assertEqual(ads[0].trade_url, "https://www.roblox.com/users/10/trade")

    def test_exact_request_needs_every_item(self):
        mine = owned(self.cat, 3, 3, 4)
        ads = self.parse(
            ad(1, 10, [2], [3, 3]),        # 50k for my two Small Hats: an even deal they asked for
            ad(2, 11, [2], [3, 3, 3]),     # wants 3 Small Hats, I only have 2
        )
        trades = match_trade_ads(mine, ads)
        self.assertEqual([t.ad.ad_id for t in trades], [1])
        self.assertEqual(trades[0].kind, UPGRADE)

    def test_exact_request_rejected_when_bad_for_me(self):
        mine = owned(self.cat, 1)
        trades = match_trade_ads(mine, self.parse(ad(1, 10, [3], [1])))  # my 100k for their 25k
        self.assertEqual(trades, [])

    def test_upgrade_tag_ad_gets_one_bigger_item(self):
        mine = owned(self.cat, 1, 2, 4)
        # They offer 50k + 25k + 25k + 10k and want an upgrade: my Big Hat (100k) fits.
        trades = match_trade_ads(mine, self.parse(ad(1, 10, [2, 3, 3, 4], tags=["upgrade"])))
        self.assertEqual(len(trades), 1)
        self.assertEqual([i.asset_id for i in trades[0].give], [1])
        self.assertEqual(trades[0].kind, DOWNGRADE)

    def test_downgrade_tag_ad_gets_several_smaller_items(self):
        mine = owned(self.cat, 2, 3, 3, 4)
        trades = match_trade_ads(mine, self.parse(ad(1, 10, [1], tags=["downgrade"])))
        self.assertEqual(len(trades), 1)
        t = trades[0]
        self.assertGreater(len(t.give), 1)
        self.assertTrue(0.80 <= t.ratio <= 0.95)

    def test_robux_counts_after_tax_and_robux_only_requests_skipped(self):
        mine = owned(self.cat, 2)
        trades = match_trade_ads(mine, self.parse(
            ad(1, 10, [3, 4], tags=["any"], robux=25000),  # 25k + 10k + 17.5k = 52.5k for my 50k
            ad(2, 11, [1], tags=["robux"]),
        ))
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].receive_value, 52500)

    def test_one_trade_per_poster(self):
        mine = owned(self.cat, 2, 3, 3, 4)
        trades = match_trade_ads(mine, self.parse(ad(1, 10, [1], tags=["any"]), ad(2, 10, [1], tags=["any"])))
        self.assertEqual(len(trades), 1)


class CliTests(unittest.TestCase):
    def run_cli(self, argv, assets, ads=()):
        def fake_assets(user_id):
            return assets[user_id], set()

        cat = catalog()
        with mock.patch.object(rolimons, "fetch_item_details", return_value=cat), \
             mock.patch.object(rolimons, "fetch_player_assets", side_effect=fake_assets), \
             mock.patch.object(tradeads, "fetch_recent_ads",
                               return_value=tradeads.parse_trade_ads({"trade_ads": list(ads)}, cat)), \
             mock.patch("trade_bot.roblox.resolve_user", side_effect=lambda s: (int(s), f"user{s}")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                cli.main(argv)
            return buf.getvalue()

    def test_partner_mode_json(self):
        out = self.run_cli(["1", "--partner", "2", "--json"], {1: {2: [1], 3: [2, 3], 4: [4]}, 2: {1: [9]}})
        data = json.loads(out)
        self.assertEqual(data["partner"]["name"], "user2")
        self.assertEqual(data["trades"]["upgrade"][0]["receive"][0]["name"], "Big Hat")

    def test_falls_back_to_market_when_no_ad_matches(self):
        out = self.run_cli(["1"], {1: {2: [1], 3: [2, 3], 4: [4]}}, ads=[ad(1, 10, [9], [1])])
        self.assertIn("Nobody in the 1 latest Rolimons trade ads", out)
        self.assertIn("Best upgrades on the Rolimons market", out)
        self.assertIn("RECEIVE", out)

    def test_shows_matching_ads(self):
        out = self.run_cli(["1"], {1: {2: [1], 3: [2, 3], 4: [4]}}, ads=[ad(7, 42, [1], tags=["downgrade"])])
        self.assertIn("Players on Rolimons who want a trade you can do now", out)
        self.assertIn("Trade ad by poster42", out)
        self.assertIn("https://www.roblox.com/users/42/trade", out)
        self.assertNotIn("Rolimons market", out)

    def test_ads_json(self):
        out = self.run_cli(["1", "--json"], {1: {2: [1], 3: [2, 3], 4: [4]}}, ads=[ad(7, 42, [1], tags=["any"])])
        data = json.loads(out)
        self.assertEqual(data["ad_trades"][0]["ad"]["username"], "poster42")
        self.assertIsNone(data["market_trades"])

    def test_interactive_app(self):
        cat = catalog()
        inputs = iter(["1", "404", ""])
        with mock.patch.object(rolimons, "fetch_item_details", return_value=cat), \
             mock.patch.object(rolimons, "fetch_player_assets", return_value=({2: [1], 3: [2, 3], 4: [4]}, set())), \
             mock.patch.object(tradeads, "fetch_recent_ads", return_value=[]), \
             mock.patch("trade_bot.roblox.resolve_user", side_effect=lambda s: (1, "alice") if s == "1"
                        else (_ for _ in ()).throw(ValueError(f"No Roblox user named {s!r}"))), \
             mock.patch("builtins.input", side_effect=lambda _: next(inputs)):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = app.main()
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Scanning 1's inventory", out)
        self.assertIn("Best upgrades on the Rolimons market", out)
        self.assertIn("No Roblox user named '404'", out)


if __name__ == "__main__":
    unittest.main()
