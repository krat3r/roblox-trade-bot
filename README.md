# roblox-trade-bot

Scans a Roblox player's limiteds and suggests the best trades using
[Rolimons](https://www.rolimons.com) values, demand and trends.

It only **suggests** trades. It never logs into your account or sends anything,
so it needs no cookie or password.

## Requirements

Python 3.10 or newer. There are no packages to install.

## Usage

```bash
# Best trades for you against the whole Rolimons market
python -m trade_bot YourUsername

# Best trades using only a specific player's inventory (a real trade partner)
python -m trade_bot YourUsername --partner TheirUsername

# Only upgrades, top 5, never trade away your Dominus
python -m trade_bot YourUsername --kind upgrade --top 5 --keep "Dominus Empyreus"

# Machine-readable output
python -m trade_bot YourUsername --json
```

You can pass a user ID instead of a username.

## How it works

1. **Values:** item values come from the Rolimons item API
   (`api.rolimons.com/items/v2/itemdetails`). The result is cached for 5 minutes in
   `~/.cache/roblox-trade-bot/`.
2. **Inventory scan:** each inventory is scanned through Rolimons
   (`api.rolimons.com/players/v1/playerassets/<id>`). If that fails, the bot falls
   back to the Roblox collectibles API. Items on trade hold are skipped, and a
   private inventory gives a clear error.
3. **Trade search:** each item is worth its Rolimons value, or its RAP if it has
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
