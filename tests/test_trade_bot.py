import io
import json
import random
import time
import unittest
from contextlib import redirect_stdout
from unittest import mock

from trade_bot import cli, rolimons
from trade_bot.engine import DOWNGRADE, SIDEGRADE, UPGRADE, ComboFinder, TradeRules, generate_trades
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


class CliTests(unittest.TestCase):
    def run_cli(self, argv, assets):
        def fake_assets(user_id):
            return assets[user_id], set()

        with mock.patch.object(rolimons, "fetch_item_details", return_value=catalog()), \
             mock.patch.object(rolimons, "fetch_player_assets", side_effect=fake_assets), \
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

    def test_market_mode_text(self):
        out = self.run_cli(["1"], {1: {2: [1], 3: [2, 3], 4: [4]}})
        self.assertIn("Best upgrades from the Rolimons market", out)
        self.assertIn("RECEIVE", out)


if __name__ == "__main__":
    unittest.main()
