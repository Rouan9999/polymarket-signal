"""Step 1 — fetch the Polymarket top-N leaderboard and store a snapshot.

Endpoint: GET https://data-api.polymarket.com/v1/leaderboard
Docs:     https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings

The API caps `limit` at 50, so we paginate (offset=0, offset=50) for top 100.

Run:
    python -m src.fetch_leaderboard
"""
import sys
import time
from datetime import datetime, timezone

import requests

from .config import (
    LEADERBOARD_CATEGORY,
    LEADERBOARD_ORDER_BY,
    LEADERBOARD_TIME_PERIOD,
    LEADERBOARD_TOP_N,
    POLY_DATA_API,
)
from .db import get_conn, init_db

PAGE_SIZE = 50      # Polymarket API max per page
TIMEOUT_S = 15
MAX_RETRIES = 3


def fetch_page(offset: int) -> list[dict]:
    """Fetch a single page of leaderboard results with exponential-backoff retry."""
    url = f"{POLY_DATA_API}/v1/leaderboard"
    params = {
        "category": LEADERBOARD_CATEGORY,
        "timePeriod": LEADERBOARD_TIME_PERIOD,
        "orderBy": LEADERBOARD_ORDER_BY,
        "limit": PAGE_SIZE,
        "offset": offset,
    }
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, params=params, timeout=TIMEOUT_S)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                raise ValueError(f"Unexpected response shape: {type(data).__name__}")
            return data
        except (requests.RequestException, ValueError) as e:
            last_err = e
            wait = 2 ** attempt
            print(f"  Attempt {attempt + 1}/{MAX_RETRIES} failed ({e}); retrying in {wait}s",
                  file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"fetch_page(offset={offset}) failed after {MAX_RETRIES} retries: {last_err}")


def fetch_top_traders(top_n: int = LEADERBOARD_TOP_N) -> list[dict]:
    """Fetch the top-N traders by paginating pages of PAGE_SIZE."""
    traders: list[dict] = []
    offset = 0
    while len(traders) < top_n:
        page = fetch_page(offset)
        if not page:
            break
        traders.extend(page)
        if len(page) < PAGE_SIZE:
            # API returned fewer than requested — no more pages.
            break
        offset += PAGE_SIZE
    return traders[:top_n]


def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _to_float(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def save_snapshot(traders: list[dict]) -> int:
    """Persist a leaderboard snapshot to the `traders` table. Returns row count."""
    today = datetime.now(timezone.utc).date().isoformat()
    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for t in traders:
        wallet = t.get("proxyWallet")
        if not wallet:
            continue
        rows.append((
            today,
            _to_int(t.get("rank")),
            wallet,
            t.get("userName"),
            _to_float(t.get("vol")),
            _to_float(t.get("pnl")),
            1 if t.get("verifiedBadge") else 0,
            fetched_at,
        ))
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO traders
               (snapshot_date, rank, proxy_wallet, user_name, vol, pnl, verified, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    print(f"Saved {len(rows)} traders for snapshot date {today}")
    return len(rows)


def main() -> int:
    init_db()
    print(
        f"Fetching top {LEADERBOARD_TOP_N} traders by {LEADERBOARD_ORDER_BY} "
        f"(window={LEADERBOARD_TIME_PERIOD}, category={LEADERBOARD_CATEGORY})..."
    )
    try:
        traders = fetch_top_traders()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not traders:
        print("No traders returned. Check API connectivity.", file=sys.stderr)
        return 1

    print(f"Got {len(traders)} traders. Top 5:")
    for t in traders[:5]:
        name = t.get("userName") or "(no name)"
        pnl = _to_float(t.get("pnl"))
        vol = _to_float(t.get("vol"))
        print(f"  #{_to_int(t.get('rank')):>3}  {name[:24]:<24}  "
              f"pnl=${pnl:>12,.0f}  vol=${vol:>12,.0f}  {t.get('proxyWallet')}")

    save_snapshot(traders)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
