"""Daily pipeline orchestrator — Steps 1-5 in sequence.

Run:
    python -m src.run_daily
"""
import sys

from . import fetch_leaderboard, fetch_positions, aggregate
from . import filter as filter_mod
from . import persist
from .db import get_conn, init_db

_SEP = "=" * 60


def run_daily() -> int:
    init_db()

    print(_SEP)
    print("Step 1: Fetching leaderboard...")
    print(_SEP)
    if fetch_leaderboard.main() != 0:
        print("ERROR: Step 1 failed.", file=sys.stderr)
        return 1

    print()
    print(_SEP)
    print("Step 2: Fetching positions...")
    print(_SEP)
    if fetch_positions.main() != 0:
        print("ERROR: Step 2 failed.", file=sys.stderr)
        return 1

    print()
    print(_SEP)
    print("Step 3: Aggregating consensus...")
    print(_SEP)
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(snapshot_date) FROM positions").fetchone()
        snapshot_date = row[0] if row else None
    if not snapshot_date:
        print("ERROR: No positions snapshot found after Step 2.", file=sys.stderr)
        return 1
    rows = aggregate.aggregate_consensus(snapshot_date)
    print(f"Aggregated {len(rows):,} unique (market, side) combos.")

    print()
    print(_SEP)
    print("Step 4: Filtering and scoring...")
    print(_SEP)
    candidates, funnel = filter_mod.filter_and_score(rows)

    print()
    print(_SEP)
    print("Step 5: Persisting signals...")
    print(_SEP)
    persist.save_funnel(funnel, snapshot_date)
    inserted, deduped = persist.save_signals(candidates)

    print(f"\nDaily run complete.")
    print(f"  New signals:  {inserted}")
    print(f"  Deduped:      {deduped}")
    print()
    persist.print_open_book()
    return 0


def main() -> int:
    return run_daily()


if __name__ == "__main__":
    raise SystemExit(main())
