"""Step 5 — persist signals and open paper positions.

Deduplication: skips any (condition_id, token_id) already signaled within
DEDUPE_WINDOW_DAYS. Ensures re-running run_daily on the same day is idempotent.

Run:
    python -m src.persist
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from .config import DEDUPE_WINDOW_DAYS, PAPER_POSITION_SIZE_USD
from .db import get_conn, init_db
from .filter import FunnelCounts, SignalCandidate


def save_funnel(funnel: FunnelCounts, snapshot_date: str) -> None:
    """Write one funnel_runs row for this pipeline execution."""
    run_at = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO funnel_runs
               (run_at, snapshot_date, starting_candidates, after_price_floor,
                after_price_ceiling, after_resolution_window, after_latency,
                after_category, after_liquidity, final_signals, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_at, snapshot_date,
                funnel.starting_candidates, funnel.after_price_floor,
                funnel.after_price_ceiling, funnel.after_resolution_window,
                funnel.after_latency, funnel.after_category,
                funnel.after_liquidity, funnel.final_signals,
                funnel.notes,
            ),
        )


def save_signals(candidates: list[SignalCandidate]) -> tuple[int, int]:
    """Insert signals + open paper positions for each non-deduped candidate.

    Each signal and its paper position are written in the same transaction —
    either both land or neither does.

    Returns (inserted, deduped).
    """
    inserted = 0
    deduped = 0
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).date().isoformat()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DEDUPE_WINDOW_DAYS)).isoformat()

    for c in candidates:
        try:
            with get_conn() as conn:
                # Dedupe check
                existing = conn.execute(
                    """SELECT signal_id FROM signals
                       WHERE condition_id = ? AND token_id = ?
                         AND created_at >= ?
                       LIMIT 1""",
                    (c.condition_id, c.token_id, cutoff),
                ).fetchone()

                if existing:
                    deduped += 1
                    print(f'  Skipping (deduped): "{c.market_question[:50]}" ({c.side})')
                    continue

                # Insert signal
                cur = conn.execute(
                    """INSERT INTO signals
                       (created_at, condition_id, token_id, market_question, side,
                        entry_price, consensus_count, consensus_size_usd,
                        days_to_resolution, bucket, score, category, market_url)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        now, c.condition_id, c.token_id, c.market_question, c.side,
                        c.current_price, c.consensus_count, c.consensus_size_usd,
                        c.days_to_resolution, c.bucket, c.score, c.category, c.market_url,
                    ),
                )
                signal_id = cur.lastrowid

                # Open paper position in the same transaction
                conn.execute(
                    """INSERT INTO paper_positions
                       (signal_id, opened_at, entry_price, size_usd, status)
                       VALUES (?, ?, ?, ?, 'OPEN')""",
                    (signal_id, today, c.current_price, PAPER_POSITION_SIZE_USD),
                )
                inserted += 1

        except Exception as e:
            print(f"  ERROR persisting signal for {c.condition_id[:20]}...: {e}",
                  file=sys.stderr)

    return inserted, deduped


def print_open_book() -> None:
    """Print the current open paper positions."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT pp.position_id, pp.signal_id, pp.opened_at,
                      pp.entry_price, pp.size_usd,
                      s.market_question, s.side
               FROM paper_positions pp
               JOIN signals s ON s.signal_id = pp.signal_id
               WHERE pp.status = 'OPEN'
               ORDER BY pp.position_id""",
        ).fetchall()

    if not rows:
        print("Open paper book: (empty)")
        return

    total_usd = sum(r["size_usd"] for r in rows)
    print("Open paper book (after this run):")
    header = (
        f"  {'pos_id':>6}  {'sig_id':>6}  {'opened':>10}  "
        f"{'entry':>5}  {'size_usd':>8}  market"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for r in rows:
        question = (r["market_question"] or "(unknown)")[:45]
        print(
            f"  {r['position_id']:>6}  {r['signal_id']:>6}  "
            f"{r['opened_at'][:10]:>10}  {r['entry_price']:>5.2f}  "
            f"${r['size_usd']:>7.2f}  \"{question}\" ({r['side']})"
        )
    print(f"\nTotal open: {len(rows)} position(s), ${total_usd:,.2f} capital deployed (paper).")


def main() -> int:
    init_db()

    with get_conn() as conn:
        row = conn.execute("SELECT MAX(snapshot_date) FROM positions").fetchone()
        snapshot_date = row[0] if row else None

    if not snapshot_date:
        print("No positions data. Run fetch_positions first.")
        return 1

    from .aggregate import aggregate_consensus
    from .filter import filter_and_score

    rows = aggregate_consensus(snapshot_date)
    candidates, funnel = filter_and_score(rows)

    print(f"\nPersisting today's signals...")
    print(f"  Candidates from Step 4:      {len(candidates)}")

    save_funnel(funnel, snapshot_date)
    inserted, deduped = save_signals(candidates)

    print(f"  Already signaled in last {DEDUPE_WINDOW_DAYS}d: {deduped}")
    print(f"  New signals written:         {inserted}")
    print(f"  Paper positions opened:      {inserted}")

    print()
    print_open_book()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
