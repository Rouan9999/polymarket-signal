# Polymarket signal system

Daily pipeline that scans the top 100 Polymarket traders, finds consensus trades among them, paper-trades the strongest signals, and (eventually) texts the top picks to your phone.

**Current mode:** paper-only. No real money until 60+ days of measured paper results.

## Quick start

```bash
# 1. Set up venv
python3 -m venv venv
source venv/bin/activate

# 2. Install deps
pip install -r requirements.txt

# 3. Copy env template (defaults are fine for step 1)
cp .env.example .env

# 4. Run step 1: fetch the leaderboard snapshot
python -m src.fetch_leaderboard
```

You should see the top 5 traders printed, and a SQLite database appear at `data/polymarket.db`.

To verify what landed in the DB:

```bash
sqlite3 data/polymarket.db "SELECT rank, user_name, pnl FROM traders ORDER BY rank LIMIT 10;"
```

## Roadmap

- [x] Project scaffold, env config, SQLite schema for the whole pipeline
- [x] **Step 1** — Fetch top 100 leaderboard (30-day PnL window) → `traders` table
- [ ] **Step 2** — Fetch active positions per wallet → `positions` table
- [ ] **Step 3** — Aggregate consensus by (market, side)
- [ ] **Step 4** — Apply filter rules (consensus, price, liquidity, days-to-resolution bucket, exclude info-edge categories)
- [ ] **Step 5** — Score, dedupe, store signal + open paper position
- [ ] **Step 6** — Twilio SMS for top picks
- [ ] **Step 7** — Daily mark-to-market + resolution logging
- [ ] **Step 8** — Weekly eval (hit rate, ROI, Brier score) → tune filter thresholds
- [ ] **Step 9** — GitHub Actions cron

## Strategy parameters

Configurable in `src/config.py`:

- **Leaderboard window:** 30 days, ordered by PnL
- **Top N:** 100
- **Resolution buckets:** 2-7 days, 8-21 days (faster feedback than long-window markets)
- **Filter rules** (step 4): min 8/100 consensus, entry price ≤ 0.80, min $50K liquidity, exclude POLITICS + SPORTS

## Database schema

See `src/db.py`. Tables:

- `traders` — daily leaderboard snapshots (rank, wallet, pnl, vol)
- `positions` — active positions per wallet per snapshot (market, side, size, entry)
- `signals` — generated signals (one row per market+side per generation day)
- `paper_positions` — paper trade book (entry, status, mark-to-market P&L, outcome)

## Reference

- Polymarket data API: https://data-api.polymarket.com
- Leaderboard endpoint docs: https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings

## Important

This is a **research project** to measure whether a consensus-copy strategy has edge. It is not investment advice and is paper-trade only by default. Do not flip `MODE=live` in `.env` without:

1. 60+ days of measured positive paper results, AND
2. Understanding the structural risks (latency penalty, survivorship bias, market reflexivity)
