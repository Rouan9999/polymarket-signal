"""Step 3 — aggregate positions into consensus rows by (market, side).

Pure read-only: no API calls, no writes. Returns an in-memory list that
Steps 4 and 5 chain on top of.

Run:
    python -m src.aggregate
"""
from __future__ import annotations

from dataclasses import dataclass

from .db import get_conn

_SQL = """
SELECT
    condition_id,
    token_id,
    MAX(market_question)                          AS market_question,
    MAX(outcome)                                  AS outcome,
    MAX(end_date)                                 AS end_date,
    MAX(days_to_resolution)                       AS days_to_resolution,
    MAX(current_price)                            AS current_price,
    COUNT(DISTINCT proxy_wallet)                  AS consensus_count,
    SUM(current_value)                            AS consensus_size_usd,
    SUM(size * avg_price) / NULLIF(SUM(size), 0) AS avg_entry_price,
    GROUP_CONCAT(proxy_wallet)                    AS wallets_holding_csv
FROM positions
WHERE snapshot_date = COALESCE(?, (SELECT MAX(snapshot_date) FROM positions))
  AND size > 0
GROUP BY condition_id, token_id
ORDER BY consensus_count DESC, consensus_size_usd DESC
"""


@dataclass(frozen=True)
class ConsensusRow:
    condition_id: str
    token_id: str
    market_question: str
    outcome: str                     # 'Yes' or 'No'
    end_date: str | None
    days_to_resolution: int | None
    current_price: float
    consensus_count: int
    consensus_size_usd: float
    avg_entry_price: float           # size-weighted avg across all holders
    wallets_holding: tuple[str, ...] # split from GROUP_CONCAT


def aggregate_consensus(snapshot_date: str | None = None) -> list[ConsensusRow]:
    """Return consensus rows for snapshot_date (defaults to latest)."""
    with get_conn() as conn:
        rows = conn.execute(_SQL, (snapshot_date,)).fetchall()

    result = []
    for r in rows:
        wallets = tuple(r["wallets_holding_csv"].split(",")) if r["wallets_holding_csv"] else ()
        result.append(ConsensusRow(
            condition_id=r["condition_id"] or "",
            token_id=r["token_id"] or "",
            market_question=r["market_question"] or "",
            outcome=r["outcome"] or "",
            end_date=r["end_date"],
            days_to_resolution=r["days_to_resolution"],
            current_price=r["current_price"] or 0.0,
            consensus_count=r["consensus_count"],
            consensus_size_usd=r["consensus_size_usd"] or 0.0,
            avg_entry_price=r["avg_entry_price"] or 0.0,
            wallets_holding=wallets,
        ))
    return result


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def _fmt_usd(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def _fmt_days(d: int | None) -> str:
    return f"{d}d" if d is not None else "--"


def _distribution(rows: list[ConsensusRow]) -> dict[str, int]:
    buckets: dict[str, int] = {"1": 0, "2-3": 0, "4-7": 0, "8-15": 0, "16+": 0}
    for r in rows:
        c = r.consensus_count
        if c == 1:
            buckets["1"] += 1
        elif c <= 3:
            buckets["2-3"] += 1
        elif c <= 7:
            buckets["4-7"] += 1
        elif c <= 15:
            buckets["8-15"] += 1
        else:
            buckets["16+"] += 1
    return buckets


def _latest_snapshot_date() -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT MAX(snapshot_date) FROM positions").fetchone()
        return row[0] if row else None


def _total_positions(snapshot_date: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM positions WHERE snapshot_date = ?",
            (snapshot_date,),
        ).fetchone()
        return row[0] if row else 0


def main() -> int:
    snapshot_date = _latest_snapshot_date()
    if not snapshot_date:
        print("No positions data found. Run fetch_positions first.")
        return 1

    rows = aggregate_consensus(snapshot_date)
    if not rows:
        print(f"No position rows found for snapshot {snapshot_date}.")
        return 1

    total_positions = _total_positions(snapshot_date)
    print(f"Snapshot: {snapshot_date}")
    print(f"Total unique (market, side) combos with positions: {len(rows):,}")
    print(f"Total positions analyzed: {total_positions:,}")

    dist = _distribution(rows)
    total = len(rows)
    print("\nConsensus distribution (number of top-100 traders on same side):")
    labels = [
        ("   1 trader", "1"),
        ("   2-3     ", "2-3"),
        ("   4-7     ", "4-7"),
        ("   8-15    ", "8-15"),
        ("   16+     ", "16+"),
    ]
    for label, key in labels:
        count = dist[key]
        pct = f"({count / total * 100:.1f}%)" if key == "1" else ""
        marker = "  <- survives MIN_CONSENSUS filter" if key == "8-15" else ""
        print(f"  {label}:  {count:>5} markets  {pct}{marker}")
    print(f"  {'':->9}   {'----':>5}")
    print(f"  {'TOTAL':>9}:  {total:>5}")

    print(f"\nTop 20 by consensus_count:")
    header = f"  {'rank':>4}  {'cnt':>3}   {'$size':>8}   {'entry->curr':>11}   {'days':>4}  market (side)"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for i, r in enumerate(rows[:20], 1):
        question = r.market_question[:52] if r.market_question else "(unknown)"
        print(
            f"  {i:>4}  {r.consensus_count:>3}   {_fmt_usd(r.consensus_size_usd):>8}   "
            f"{r.avg_entry_price:.2f} -> {r.current_price:.2f}   "
            f"{_fmt_days(r.days_to_resolution):>4}  "
            f'"{question}" ({r.outcome})'
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
