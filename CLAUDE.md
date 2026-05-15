# Polymarket Signal System — Claude Context

## What this project is
Daily pipeline that scans the top 100 Polymarket traders, finds consensus trades among them, paper-trades the strongest signals, and emails top picks. **Paper-only mode until 60+ days of measured edge.**

## Project layout
```
gamefiles/
  src/
    __init__.py
    config.py               # all tunable constants + env vars
    db.py                   # SQLite schema + connection helper
    fetch_leaderboard.py    # Step 1
    fetch_positions.py      # Step 2
    aggregate.py            # Step 3
    filter.py               # Step 4  (also defines SignalCandidate, FunnelCounts)
    persist.py              # Step 5  (save_signals, save_funnel, print_open_book)
    notify.py               # Step 6  (Gmail email, --test flag)
    run_daily.py            # Orchestrator: Steps 1-5 in sequence
  data/
    polymarket.db           # SQLite DB (gitignored)
  venv/                     # Python venv — activate with .\venv\Scripts\Activate.ps1
  .env                      # real credentials (gitignored)
  .env.example              # template
  requirements.txt
```

## Completed steps
- [x] **Scaffold** — src/ package, .env, SQLite schema (traders, positions, signals, paper_positions, funnel_runs)
- [x] **Step 1** — `fetch_leaderboard.py`: top 100 traders by 30d PnL → `traders` table
- [x] **Step 2** — `fetch_positions.py`: active positions per wallet → `positions` table (~6K rows)
- [x] **Step 3** — `aggregate.py`: group by (condition_id, token_id) → `ConsensusRow` list
- [x] **Step 4** — `filter.py`: 7-stage funnel → `SignalCandidate` list + `FunnelCounts`
- [x] **Step 5** — `persist.py`: dedupe + write `signals` + open `paper_positions`; `funnel_runs` telemetry
- [x] **Step 6** — `notify.py`: Gmail SMTP email digest; `--test` flag for config validation

## Roadmap (what's next)
- [ ] **Step 7** — Daily mark-to-market + resolution logging (`paper_positions` close logic)
- [ ] **Step 8** — Weekly eval: hit rate, ROI, Brier score → tune filter thresholds
- [ ] **Step 9** — GitHub Actions cron (daily `run_daily.py`, secrets from repo env vars)

## How to run
```powershell
# Activate venv
.\venv\Scripts\Activate.ps1

# Full daily pipeline (Steps 1-5)
python -m src.run_daily

# Individual steps
python -m src.fetch_leaderboard
python -m src.fetch_positions
python -m src.aggregate
python -m src.filter
python -m src.persist

# Test email config (after filling .env)
python -m src.notify --test
```

## Key config (src/config.py)
| Constant | Value | Meaning |
|---|---|---|
| LEADERBOARD_TOP_N | 100 | traders fetched |
| LEADERBOARD_TIME_PERIOD | MONTH | 30-day PnL window |
| MIN_CONSENSUS | 8 | min traders on same side |
| MIN_ENTRY_PRICE | 0.10 | lottery-ticket floor |
| MAX_ENTRY_PRICE | 0.80 | skip near-certainty |
| MAX_LATENCY_PENALTY | 0.25 | max price move since smart-money entry |
| MIN_LIQUIDITY_USD | 50,000 | min market liquidity |
| EXCLUDED_CATEGORIES | POLITICS, SPORTS | info-edge heavy |
| PAPER_POSITION_SIZE_USD | 100.0 | flat size per signal |
| DEDUPE_WINDOW_DAYS | 7 | suppress repeat signals on same market |

## Email config (.env)
```
GMAIL_USER=novabuiltbusiness@gmail.com    # sender
GMAIL_APP_PASSWORD=<16-char app password> # from myaccount.google.com/apppasswords
EMAIL_TO=beati.nandjou@gmail.com          # recipient
```
App password requires 2-Step Verification enabled on the sending account.

## Polymarket APIs
- Data API: `https://data-api.polymarket.com`
  - Leaderboard: `GET /v1/leaderboard` (paginated, limit=50)
  - Positions:   `GET /positions?user={wallet}&limit=500`
- Gamma API: `https://gamma-api.polymarket.com`
  - Markets:     `GET /markets` with repeated `?condition_ids=` params (batch)
  - Fields used: `liquidityNum`, `volume24hr`, `slug`, `feeType`, `events[0].slug`

## Filter funnel (Step 4)
Cheap filters first (no API), then one Gamma API batch call for survivors only:
1. consensus_count >= 8
2. current_price >= 0.10
3. current_price <= 0.80
4. 2 <= days_to_resolution <= 21 (must fit BUCKETS)
5. current_price - avg_entry_price <= 0.25
6. category not in {POLITICS, SPORTS}  ← Gamma API enrichment starts here
7. liquidityNum >= 50,000

## Strategy note
Consensus-copy: if ≥8 of the top 100 traders hold the same side of a market,
that's a signal. Hypothesis: smart-money consensus has edge in short-resolution
markets (2–21 days) outside politics/sports. Paper-trading to measure this first.
