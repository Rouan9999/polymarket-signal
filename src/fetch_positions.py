"""Step 2 — fetch active positions per wallet and store a snapshot.

Endpoint: GET https://data-api.polymarket.com/positions
Note: no /v1/ prefix — different base path from the leaderboard endpoint.

Run:
    python -m src.fetch_positions
"""
import sys
import time
from datetime import datetime, timezone

import requests

from .config import POLY_DATA_API
from .db import get_conn, init_db

POSITIONS_URL = f"{POLY_DATA_API}/positions"
TIMEOUT_S = 15
MAX_RETRIES = 3
SLEEP_BETWEEN_S = 0.05   # 50 ms — be a good API citizen across 100 requests
ABORT_THRESHOLD = 20      # abort early if this many wallets fail


def _to_float(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _days_to_resolution(end_date_str: str | None) -> int | None:
    """Integer days from today UTC to endDate. Returns None if missing or past."""
    if not end_date_str:
        return None
    try:
        end = datetime.strptime(end_date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        days = (end - today).days
        return days if days >= 0 else None
    except (ValueError, AttributeError):
        return None


def fetch_positions(proxy_wallet: str) -> list[dict]:
    """Fetch active positions for one wallet with exponential-backoff retry."""
    params = {
        "user": proxy_wallet,
        "sizeThreshold": 1,
        "redeemable": "false",
        "limit": 500,
        "sortBy": "CURRENT",
    }
    last_err: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(POSITIONS_URL, params=params, timeout=TIMEOUT_S)
            r.raise_for_status()
            data = r.json()
            if not isinstance(data, list):
                raise ValueError(f"Unexpected response shape: {type(data).__name__}")
            return data
        except (requests.RequestException, ValueError) as e:
            last_err = e
            wait = 2 ** attempt
            print(f"    Attempt {attempt + 1}/{MAX_RETRIES} failed ({e}); retrying in {wait}s",
                  file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(
        f"fetch_positions({proxy_wallet}) failed after {MAX_RETRIES} retries: {last_err}"
    )


def _load_latest_wallets() -> tuple[str, list[str]]:
    """Return (snapshot_date, [proxy_wallet, ...]) for the most recent leaderboard snapshot."""
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(snapshot_date) FROM traders").fetchone()
        if not row or not row[0]:
            raise RuntimeError("No trader snapshots found. Run fetch_leaderboard first.")
        snapshot_date = row[0]
        wallets = [
            r[0] for r in conn.execute(
                "SELECT proxy_wallet FROM traders WHERE snapshot_date = ? ORDER BY rank",
                (snapshot_date,),
            ).fetchall()
        ]
    return snapshot_date, wallets


def _build_rows(
    positions: list[dict],
    proxy_wallet: str,
    snapshot_date: str,
    fetched_at: str,
) -> list[tuple]:
    rows = []
    for p in positions:
        token_id = p.get("asset")
        if not token_id:
            continue
        end_date = p.get("endDate")
        rows.append((
            snapshot_date,
            proxy_wallet,
            p.get("conditionId"),
            token_id,
            p.get("title"),
            p.get("outcome"),
            _to_float(p.get("size")),
            _to_float(p.get("avgPrice")),
            _to_float(p.get("curPrice")),
            _to_float(p.get("currentValue")),
            _to_float(p.get("initialValue")),
            _to_float(p.get("cashPnl")),
            _to_float(p.get("percentPnl")),
            _days_to_resolution(end_date),
            end_date,
            None,   # category — backfilled in step 3
            fetched_at,
        ))
    return rows


def _save_positions(rows: list[tuple]) -> int:
    if not rows:
        return 0
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO positions
               (snapshot_date, proxy_wallet, condition_id, token_id, market_question,
                outcome, size, avg_price, current_price, current_value, initial_value,
                cash_pnl, percent_pnl, days_to_resolution, end_date, category, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
    return len(rows)


def _print_consensus_preview(snapshot_date: str) -> None:
    with get_conn() as conn:
        top = conn.execute(
            """SELECT market_question, outcome,
                      COUNT(DISTINCT proxy_wallet) AS traders,
                      SUM(current_value)           AS total_usd
               FROM positions
               WHERE snapshot_date = ?
               GROUP BY condition_id, token_id, outcome
               ORDER BY traders DESC, total_usd DESC
               LIMIT 3""",
            (snapshot_date,),
        ).fetchall()
    print("Top 3 markets by consensus count among top 100:")
    for r in top:
        question = (r[0] or "(unknown)")[:60]
        print(f'  - "{question}" ({r[1]}): {r[2]} traders, ${r[3]:,.0f}')


def fetch_and_store_positions() -> int:
    """Fetch positions for the latest leaderboard snapshot. Returns exit code."""
    init_db()

    try:
        snapshot_date, wallets = _load_latest_wallets()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    total = len(wallets)
    print(f"Fetching positions for {total} wallets (snapshot {snapshot_date})...")

    fetched_at = datetime.now(timezone.utc).isoformat()
    all_rows: list[tuple] = []
    succeeded = 0
    no_positions = 0
    failed = 0

    for i, wallet in enumerate(wallets, 1):
        try:
            positions = fetch_positions(wallet)
            if positions:
                rows = _build_rows(positions, wallet, snapshot_date, fetched_at)
                all_rows.extend(rows)
                succeeded += 1
                print(f"  [{i}/{total}] {wallet}  {len(rows)} positions")
            else:
                no_positions += 1
                print(f"  [{i}/{total}] {wallet}  no active positions")
        except RuntimeError as e:
            failed += 1
            print(f"  [{i}/{total}] {wallet}  FAILED: {e}", file=sys.stderr)
            if failed > ABORT_THRESHOLD:
                print(
                    f"ERROR: {failed} wallets failed (threshold {ABORT_THRESHOLD}). "
                    "Aborting — check API connectivity.",
                    file=sys.stderr,
                )
                return 1
        time.sleep(SLEEP_BETWEEN_S)

    saved = _save_positions(all_rows)
    print(
        f"\nDone: {succeeded} wallets succeeded, {no_positions} with no positions, "
        f"{failed} failed."
    )
    print(f"Saved {saved} position rows.")

    if saved > 0:
        print()
        _print_consensus_preview(snapshot_date)

    return 0


def main() -> int:
    return fetch_and_store_positions()


if __name__ == "__main__":
    raise SystemExit(main())
