"""Project configuration loaded from environment and constants."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# API + storage
POLY_DATA_API = os.getenv("POLY_DATA_API", "https://data-api.polymarket.com")
DB_PATH = ROOT / os.getenv("DB_PATH", "data/polymarket.db")
MODE = os.getenv("MODE", "paper")

# Leaderboard fetch parameters
LEADERBOARD_TOP_N = 100
LEADERBOARD_TIME_PERIOD = "MONTH"   # DAY | WEEK | MONTH | ALL
LEADERBOARD_ORDER_BY = "PNL"        # PNL | VOL
LEADERBOARD_CATEGORY = "OVERALL"    # OVERALL | POLITICS | SPORTS | CRYPTO | ...

# Resolution buckets (days). Markets outside these windows are skipped.
BUCKETS = {
    "2-7d": (2, 7),
    "8-21d": (8, 21),
}

# Filter rules (used in step 4 — kept here so all params live in one place)
MIN_CONSENSUS = 8           # min top-100 traders on same side
MIN_ENTRY_PRICE = 0.10      # lottery-ticket floor
MAX_ENTRY_PRICE = 0.80      # don't buy near certainty
MAX_LATENCY_PENALTY = 0.25  # max gap between top traders' avg entry and current price
MIN_LIQUIDITY_USD = 50_000  # min market liquidity
EXCLUDED_CATEGORIES = {"POLITICS", "SPORTS"}  # info-edge heavy

# Paper trading
PAPER_POSITION_SIZE_USD = 100.0   # flat per-signal size in paper book
DEDUPE_WINDOW_DAYS = 7            # don't re-signal the same market within this window

# Email notifications (Step 6 — fill in .env before use)
GMAIL_USER = os.getenv("GMAIL_USER", "")           # sender address
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")  # 16-char app password
EMAIL_TO = os.getenv("EMAIL_TO", "")               # recipient address
