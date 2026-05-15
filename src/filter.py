"""Step 4 — filter ConsensusRows and enrich surviving candidates.

Filter order (cheap before expensive):
  1. Min consensus       >= MIN_CONSENSUS
  2. Price floor         >= MIN_ENTRY_PRICE
  3. Price ceiling       <= MAX_ENTRY_PRICE
  4. Resolution window   2-21d (must fall in a BUCKETS range)
  5. Latency penalty     current_price - avg_entry_price <= MAX_LATENCY_PENALTY
  -- Gamma API enrichment for survivors --
  6. Category exclusion  not in EXCLUDED_CATEGORIES
  7. Min liquidity       >= MIN_LIQUIDITY_USD

Run:
    python -m src.filter
"""
from __future__ import annotations

import math
import sys
from collections import Counter
from dataclasses import dataclass, field

import requests

from .aggregate import ConsensusRow, aggregate_consensus
from .config import (
    BUCKETS,
    EXCLUDED_CATEGORIES,
    MAX_ENTRY_PRICE,
    MAX_LATENCY_PENALTY,
    MIN_CONSENSUS,
    MIN_ENTRY_PRICE,
    MIN_LIQUIDITY_USD,
)
from .db import get_conn, init_db

GAMMA_API = "https://gamma-api.polymarket.com"
POLY_EVENT_BASE = "https://polymarket.com/event"
TIMEOUT_S = 15


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SignalCandidate:
    # From consensus row
    condition_id: str
    token_id: str
    market_question: str
    side: str                     # 'Yes' | 'No'
    current_price: float
    avg_entry_price: float
    consensus_count: int
    consensus_size_usd: float
    days_to_resolution: int
    bucket: str                   # '2-7d' | '8-21d'
    wallets_holding: tuple[str, ...]
    # Enriched from Gamma API
    category: str
    liquidity_usd: float
    volume_24h: float
    market_slug: str
    market_url: str
    # Computed
    score: float


@dataclass
class FunnelCounts:
    starting_candidates: int = 0
    after_price_floor: int = 0
    after_price_ceiling: int = 0
    after_resolution_window: int = 0
    after_latency: int = 0
    after_category: int = 0
    after_liquidity: int = 0
    final_signals: int = 0
    notes: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assign_bucket(days: int) -> str | None:
    for name, (lo, hi) in BUCKETS.items():
        if lo <= days <= hi:
            return name
    return None


def _infer_category(market: dict) -> str:
    """Derive category from feeType, falling back to keyword matching on event metadata."""
    fee_type = (market.get("feeType") or "").lower()
    if "sports" in fee_type:
        return "SPORTS"
    if "politics" in fee_type:
        return "POLITICS"
    if "crypto" in fee_type:
        return "CRYPTO"

    event = (market.get("events") or [{}])[0]
    text = " ".join([
        event.get("ticker", ""),
        event.get("slug", ""),
        event.get("title", ""),
        market.get("slug", ""),
        market.get("question", ""),
    ]).lower()

    sports_kw = {
        "soccer", "football", "nba", "nfl", "mlb", "nhl", "tennis", "olympic",
        "world-cup", "fifa", "champions", "basketball", "baseball", "hockey",
        "racing", "ufc", "boxing", "golf", "cricket", "rugby",
    }
    politics_kw = {
        "election", "president", "senate", "congress", "vote", "democrat",
        "republican", "trump", "biden", "kamala", "governor", "parliament",
    }
    crypto_kw = {
        "bitcoin", "ethereum", "crypto", "btc", "eth", "solana", "defi",
        "blockchain",
    }

    if any(kw in text for kw in sports_kw):
        return "SPORTS"
    if any(kw in text for kw in politics_kw):
        return "POLITICS"
    if any(kw in text for kw in crypto_kw):
        return "CRYPTO"
    return "OTHER"


def _build_market_url(market: dict) -> str:
    market_slug = market.get("slug", "")
    events = market.get("events") or []
    if events:
        event_slug = events[0].get("slug", "")
        if event_slug and market_slug:
            return f"{POLY_EVENT_BASE}/{event_slug}/{market_slug}"
    return f"https://polymarket.com/market/{market_slug}"


def _enrich_batch(condition_ids: list[str]) -> dict[str, dict]:
    """Fetch Gamma API market data for a list of condition_ids in one request.
    Returns {condition_id: market_dict}. Empty dict on total failure.
    """
    if not condition_ids:
        return {}
    params = [("condition_ids", cid) for cid in condition_ids]
    try:
        r = requests.get(f"{GAMMA_API}/markets", params=params, timeout=TIMEOUT_S)
        r.raise_for_status()
        markets = r.json()
        if not isinstance(markets, list):
            raise ValueError(f"Unexpected response shape: {type(markets).__name__}")
    except (requests.RequestException, ValueError) as e:
        print(f"  WARNING: Gamma API enrichment failed: {e}", file=sys.stderr)
        return {}
    return {m["conditionId"]: m for m in markets if "conditionId" in m}


def _compute_score(row: ConsensusRow) -> float:
    return (
        row.consensus_count
        * math.log10(max(row.consensus_size_usd, 1) + 1)
        * (1.0 - row.current_price)
        * (1.0 - (row.current_price - row.avg_entry_price))
    )


# ---------------------------------------------------------------------------
# Main filter pipeline
# ---------------------------------------------------------------------------

def filter_and_score(
    rows: list[ConsensusRow],
) -> tuple[list[SignalCandidate], FunnelCounts]:
    """Apply all filter rules. Returns (candidates sorted by score desc, funnel counts)."""
    funnel = FunnelCounts()

    # -- Stage 1: consensus threshold --
    stage = [r for r in rows if r.consensus_count >= MIN_CONSENSUS]
    funnel.starting_candidates = len(stage)
    print(f"Starting candidates (consensus >= {MIN_CONSENSUS}): {len(stage)}")

    # -- Stage 2: price floor --
    stage = [r for r in stage if r.current_price >= MIN_ENTRY_PRICE]
    funnel.after_price_floor = len(stage)
    print(f"After price floor  (>= {MIN_ENTRY_PRICE:.2f}):          {len(stage)}")

    # -- Stage 3: price ceiling --
    stage = [r for r in stage if r.current_price <= MAX_ENTRY_PRICE]
    funnel.after_price_ceiling = len(stage)
    print(f"After price ceiling(<= {MAX_ENTRY_PRICE:.2f}):          {len(stage)}")

    # -- Stage 4: resolution window --
    stage = [
        r for r in stage
        if r.days_to_resolution is not None
        and _assign_bucket(r.days_to_resolution) is not None
    ]
    funnel.after_resolution_window = len(stage)
    print(f"After resolution window (2-21d):       {len(stage)}")

    # -- Stage 5: latency penalty --
    stage = [
        r for r in stage
        if (r.current_price - r.avg_entry_price) <= MAX_LATENCY_PENALTY
    ]
    funnel.after_latency = len(stage)
    print(f"After latency penalty  (<= {MAX_LATENCY_PENALTY:.2f}):    {len(stage)}")

    if not stage:
        print(f"After category enrichment:             0 (skipped)")
        print(f"After liquidity (>= ${MIN_LIQUIDITY_USD:,.0f}):   0")
        return [], funnel

    # -- Enrich via Gamma API --
    enrichment = _enrich_batch([r.condition_id for r in stage])

    # -- Stage 6: category exclusion --
    excluded_upper = {c.upper() for c in EXCLUDED_CATEGORIES}
    cat_dropped: Counter[str] = Counter()
    after_cat: list[tuple[ConsensusRow, dict]] = []

    for r in stage:
        m = enrichment.get(r.condition_id)
        if m is None:
            print(f"  WARNING: no enrichment for {r.condition_id[:20]}... — dropping",
                  file=sys.stderr)
            cat_dropped["NO_DATA"] += 1
            continue
        cat = _infer_category(m)
        if cat.upper() in excluded_upper:
            cat_dropped[cat] += 1
        else:
            after_cat.append((r, m))

    funnel.after_category = len(after_cat)
    n_dropped = len(stage) - len(after_cat)
    dropped_detail = ", ".join(f"{cat} x{cnt}" for cat, cnt in cat_dropped.most_common())
    dropped_str = f" ({n_dropped} dropped: {dropped_detail})" if n_dropped else ""
    print(f"After category enrichment:             {len(after_cat)}{dropped_str}")

    # -- Stage 7: liquidity --
    after_liq: list[tuple[ConsensusRow, dict]] = []
    for r, m in after_cat:
        liq = m.get("liquidityNum")
        if liq is None:
            print(f"  WARNING: missing liquidityNum for {r.condition_id[:20]}... — dropping",
                  file=sys.stderr)
            continue
        if float(liq) >= MIN_LIQUIDITY_USD:
            after_liq.append((r, m))

    funnel.after_liquidity = len(after_liq)
    print(f"After liquidity (>= ${MIN_LIQUIDITY_USD:,.0f}):   {len(after_liq)}")

    # -- Build SignalCandidates --
    candidates: list[SignalCandidate] = []
    for r, m in after_liq:
        bucket = _assign_bucket(r.days_to_resolution)
        assert bucket is not None
        candidates.append(SignalCandidate(
            condition_id=r.condition_id,
            token_id=r.token_id,
            market_question=r.market_question,
            side=r.outcome,
            current_price=r.current_price,
            avg_entry_price=r.avg_entry_price,
            consensus_count=r.consensus_count,
            consensus_size_usd=r.consensus_size_usd,
            days_to_resolution=r.days_to_resolution,
            bucket=bucket,
            wallets_holding=r.wallets_holding,
            category=_infer_category(m),
            liquidity_usd=float(m.get("liquidityNum") or 0.0),
            volume_24h=float(m.get("volume24hr") or 0.0),
            market_slug=m.get("slug", ""),
            market_url=_build_market_url(m),
            score=_compute_score(r),
        ))

    candidates.sort(key=lambda c: c.score, reverse=True)
    funnel.final_signals = len(candidates)
    return candidates, funnel


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _fmt_usd(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def main() -> int:
    init_db()

    with get_conn() as conn:
        row = conn.execute("SELECT MAX(snapshot_date) FROM positions").fetchone()
        snapshot_date = row[0] if row else None

    if not snapshot_date:
        print("No positions data found. Run fetch_positions first.")
        return 1

    print(f"Snapshot: {snapshot_date}")
    rows = aggregate_consensus(snapshot_date)
    if not rows:
        print("No consensus rows found.")
        return 1

    print()
    candidates, funnel = filter_and_score(rows)

    # Persist funnel counts (local import avoids circular at module level)
    from .persist import save_funnel
    save_funnel(funnel, snapshot_date)

    print()
    if not candidates:
        print("FINAL SIGNALS: 0")
        print("(No-signal day. This is correct behavior, not a bug.)")
        return 0

    print(f"FINAL SIGNALS: {len(candidates)}")
    header = (
        f"  {'score':>5}  {'cnt':>3}  {'$size':>7}  "
        f"{'price(entry)':>12}  {'days':>4}  {'bucket':>5}  {'category':<10}  market"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for c in candidates:
        question = c.market_question[:45] if c.market_question else "(unknown)"
        print(
            f"  {c.score:>5.1f}  {c.consensus_count:>3}  "
            f"{_fmt_usd(c.consensus_size_usd):>7}  "
            f"{c.current_price:.2f} ({c.avg_entry_price:.2f})  "
            f"{c.days_to_resolution:>4}d  {c.bucket:>5}  "
            f"{c.category:<10}  "
            f'"{question}" ({c.side})'
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
