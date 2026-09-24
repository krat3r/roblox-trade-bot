# roblox-trade-bot

Scans a Roblox player's limiteds and suggests the best trades using
[Rolimons](https://www.rolimons.com) values, demand and trends.

It only **suggests** trades. It never logs into your account or sends anything,
so it needs no cookie or password.

## Download

* **Windows:** [RolimonsTradeBot.exe](https://github.com/krat3r/roblox-trade-bot/releases/latest/download/RolimonsTradeBot.exe).
  Double-click it. You don't need Python.
* **Any OS with Python 3.10+:** [roblox-trade-bot-python.zip](https://github.com/krat3r/roblox-trade-bot/releases/latest/download/roblox-trade-bot-python.zip).
  Unzip it and double-click `RolimonsTradeBot.py`.

The `.exe` isn't code-signed, so Windows SmartScreen may say "Windows protected
your PC". Click **More info → Run anyway**. GitHub Actions builds the `.exe`
from this repo's source on every push (`.github/workflows/build-exe.yml`).

## Usage

When it starts, the bot asks for a Roblox username. Then it:

1. Scans that player's limiteds on Rolimons.
2. Checks the latest **Rolimons trade ads** for players who want a trade this
   inventory can do right now. For each match it shows what to give and what
   you get, plus a link to send the trade.
3. If no one's asking, falls back to the **best trades on the Rolimons market**
   (upgrades, downgrades and 1-for-1s) to look for.
4. Shows the **best trades for long-term growth**: the trades where what you
   receive is likeliest to gain value compared to what you give.

### Command line

Everything also works from a terminal:

```bash
python -m trade_bot                                       # interactive
python -m trade_bot YourUsername                          # trade ads, else market trades
python -m trade_bot YourUsername --partner TheirUsername  # trades using only their inventory
python -m trade_bot YourUsername --kind upgrade --top 5 --keep "Dominus Empyreus"
python -m trade_bot YourUsername --json
```

## How it works

1. **Values:** item values come from the Rolimons item API
   (`api.rolimons.com/items/v2/itemdetails`). The result is cached for 5 minutes in
   `~/.cache/roblox-trade-bot/`.
2. **Inventory scan:** each inventory is scanned through Rolimons
   (`api.rolimons.com/players/v1/playerassets/<id>`). If that fails, the bot falls
   back to the Roblox collectibles API. Items on trade hold are skipped, and a
   private inventory gives a clear error.
3. **Trade ads:** the bot reads `api.rolimons.com/tradeads/v1/getrecentads` and
   checks each ad against your inventory:
   * **Ads that name specific items:** you must own every requested item, and the
     deal can't cost you more than a normal overpay.
   * **Ads with tags only** ("any", "upgrade", "downgrade", "rares" and so on): the
     bot builds the best offer from your items that fits the tag and the value
     windows below.

   Robux in an ad counts at 70%, after Roblox's trade tax. Each poster shows up
   at most once.
4. **Market trades (fallback):** each item is worth its Rolimons value, or its RAP if it has
   no value. The bot searches three kinds of trade:

   | Kind      | Shape                         | Default window (receive ÷ give) |
   |-----------|-------------------------------|---------------------------------|
   | upgrade   | 2–4 of yours → 1 bigger item  | 0.80 – 0.95 (you overpay 5–20%) |
   | downgrade | 1 of yours → 2–4 smaller items | 1.05 – 1.20 (they overpay 5–20%) |
   | sidegrade | 1 for 1                       | 0.97 – 1.08                     |

   The windows keep suggestions realistic, since a trade that robs the other
   player won't be accepted. Inside a window, the trade that's best for you
   ranks first. Demand, trend and rarity also adjust the ranking, so a
   high-demand item that's raising beats a dead item at the same value.

### Growth predictions

Every trade shows a **growth outlook** (Strong / Good / Neutral / Weak / Poor).
It compares how likely what you receive is to rise against what you give.
Rolimons' public API has no price history, so the outlook is an estimate
built from these signals:

| Signal | Effect |
|---|---|
| Rolimons trend | raising ↑, lowering ↓↓, unstable/fluctuating ↓ |
| Demand | Amazing/High ↑, Low/Terrible ↓ |
| RAP vs value | RAP above value means buyers pay more than the value, which tends to get raised ↑ (and vice versa) |
| Rare | ↑ |
| Hyped / projected | ↓ (hype fades, projected RAP crashes) |
| RAP momentum | real RAP change since the oldest day the bot has saved |

The bot saves every item's RAP once a day in
`~/.cache/roblox-trade-bot/rap_history.json` (45 days are kept). After a few
days of use, real momentum feeds into the outlook too. For the growth list, the
bot searches your trades again. This time it prefers giving away items that are
falling and receiving items that are rising, while staying inside the same fair
value windows.

This is a prediction, not a guarantee. Roblox prices can move on things no
data shows, like a Roblox event, a rerelease, or someone buying up an item.

By default the bot skips these items:

- Items Rolimons flags as **projected** (their RAP is manipulated), on both sides.
  Use `--allow-projected` to include them.
- Items flagged as **hyped** on the side you receive. Use `--allow-hyped` to include them.
- In market mode, items below **Normal** demand on the side you receive.
  Use `--min-demand` to change that.

Run `python -m trade_bot --help` for every option, including the ratio windows
(`--upgrade-ratio 0.85 0.95`, for example).

## Tests

```bash
python -m unittest -v
```

The tests use mocked API responses, so they run offline.
