"""Step 6 — email notification for daily signals.

Sends a formatted summary email via Gmail SMTP (STARTTLS on port 587).
Credentials come entirely from .env — no values are hardcoded here.

Run test:
    python -m src.notify --test
"""
from __future__ import annotations

import smtplib
import sys
from datetime import datetime, timezone
from email.message import EmailMessage

from .config import EMAIL_TO, GMAIL_APP_PASSWORD, GMAIL_USER
from .filter import SignalCandidate

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


# ---------------------------------------------------------------------------
# Core send
# ---------------------------------------------------------------------------

def send_email(subject: str, body: str) -> None:
    """Send a plain-text email via Gmail SMTP with STARTTLS.

    From: GMAIL_USER   (set in .env)
    To:   EMAIL_TO     (set in .env)
    """
    msg = EmailMessage()
    msg["From"] = GMAIL_USER
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        smtp.send_message(msg)


# ---------------------------------------------------------------------------
# Email content builders
# ---------------------------------------------------------------------------

def build_signal_email(
    candidates: list[SignalCandidate],
    snapshot_date: str,
) -> tuple[str, str]:
    """Return (subject, plain-text body) for today's signal digest."""
    if not candidates:
        subject = f"Polymarket Signal — No signals today ({snapshot_date})"
        body = (
            f"Daily run complete for {snapshot_date}.\n\n"
            "No markets passed all filter criteria today. "
            "The system only fires when smart-money consensus aligns on a "
            "short-window, non-sports/politics market.\n\n"
            "Check back tomorrow."
        )
        return subject, body

    subject = f"Polymarket Signal — {len(candidates)} signal(s) for {snapshot_date}"

    lines = [
        f"Polymarket Signal Report — {snapshot_date}",
        f"{len(candidates)} market(s) passed all filters.\n",
        f"{'#':<3}  {'Score':>5}  {'Cnt':>3}  {'Price':>5}  "
        f"{'Days':>4}  {'Bucket':>5}  {'Category':<10}  Market",
        "-" * 80,
    ]
    for i, c in enumerate(candidates, 1):
        question = (c.market_question or "(unknown)")[:50]
        lines.append(
            f"{i:<3}  {c.score:>5.1f}  {c.consensus_count:>3}  "
            f"{c.current_price:>5.2f}  {c.days_to_resolution:>4}d  "
            f"{c.bucket:>5}  {c.category:<10}  \"{question}\" ({c.side})"
        )

    lines.append("\n--- Full details ---")
    for i, c in enumerate(candidates, 1):
        lines += [
            f"\nSignal {i}: \"{c.market_question}\" ({c.side})",
            f"  Price:      {c.current_price:.2f}  "
            f"(smart-money avg entry: {c.avg_entry_price:.2f})",
            f"  Consensus:  {c.consensus_count}/100 top traders",
            f"  Size:       ${c.consensus_size_usd:,.0f} total at stake",
            f"  Resolution: {c.days_to_resolution}d ({c.bucket})",
            f"  Liquidity:  ${c.liquidity_usd:,.0f}",
            f"  Category:   {c.category}",
            f"  Score:      {c.score:.2f}",
            f"  URL:        {c.market_url}",
        ]

    lines += [
        "\n---",
        "Paper-trading signal only. No real money is at risk.",
    ]
    return subject, "\n".join(lines)


def notify_signals(candidates: list[SignalCandidate], snapshot_date: str) -> None:
    """Build and send the daily signal digest. Called from run_daily."""
    subject, body = build_signal_email(candidates, snapshot_date)
    send_email(subject, body)
    print(f"Email sent -> {EMAIL_TO}: {subject}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _check_config() -> list[str]:
    """Return a list of missing config problems, empty if all good."""
    problems = []
    if not GMAIL_USER:
        problems.append("GMAIL_USER is not set in .env")
    if not GMAIL_APP_PASSWORD:
        problems.append("GMAIL_APP_PASSWORD is not set in .env")
    elif len(GMAIL_APP_PASSWORD.replace(" ", "")) != 16:
        problems.append(
            f"GMAIL_APP_PASSWORD looks wrong: expected 16 chars, "
            f"got {len(GMAIL_APP_PASSWORD.replace(' ', ''))} "
            "(strip spaces before pasting)"
        )
    if not EMAIL_TO:
        problems.append("EMAIL_TO is not set in .env")
    return problems


def main() -> int:
    if "--test" not in sys.argv:
        print("Usage: python -m src.notify --test")
        print("(For real sends, notify is called from run_daily.py)")
        return 0

    print("Testing Gmail SMTP configuration...")
    print(f"  GMAIL_USER:  {GMAIL_USER or '(not set)'}")
    print(f"  EMAIL_TO:    {EMAIL_TO or '(not set)'}")
    print(f"  SMTP:        {SMTP_HOST}:{SMTP_PORT} (STARTTLS)")
    print(f"  APP_PW set:  {'yes' if GMAIL_APP_PASSWORD else 'NO'}")
    print()

    problems = _check_config()
    if problems:
        for p in problems:
            print(f"  ERROR: {p}", file=sys.stderr)
        return 1

    subject = (
        f"[TEST] Polymarket Signal — connection test "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    )
    body = (
        "This is a test email from the Polymarket Signal system.\n\n"
        "If you're reading this, Gmail SMTP is configured correctly.\n\n"
        f"  From (GMAIL_USER): {GMAIL_USER}\n"
        f"  To   (EMAIL_TO):   {EMAIL_TO}\n"
        f"  SMTP:              {SMTP_HOST}:{SMTP_PORT}\n"
    )

    try:
        send_email(subject, body)
        print(f"Test email sent successfully to {EMAIL_TO}.")
        print("Check the inbox (and spam folder) to confirm delivery.")
        return 0

    except smtplib.SMTPAuthenticationError:
        print(
            "ERROR: SMTP authentication failed.\n"
            "  Checklist:\n"
            "    1. Is GMAIL_USER correct? (full address, e.g. you@gmail.com)\n"
            "    2. Is GMAIL_APP_PASSWORD the 16-char app password, not your login password?\n"
            "    3. Is 2-Step Verification enabled on the sending account?\n"
            "    4. Was the app password generated at "
            "myaccount.google.com/apppasswords?\n"
            "    5. Did you paste the password without spaces into .env?",
            file=sys.stderr,
        )
        return 1

    except smtplib.SMTPRecipientsRefused:
        print(f"ERROR: Recipient refused: {EMAIL_TO}", file=sys.stderr)
        return 1

    except smtplib.SMTPSenderRefused:
        print(
            f"ERROR: Sender refused: {GMAIL_USER}\n"
            "  This can happen if Google has temporarily blocked the account "
            "for suspicious activity. Check gmail.com for a security alert.",
            file=sys.stderr,
        )
        return 1

    except smtplib.SMTPException as e:
        print(f"ERROR: SMTP error: {e}", file=sys.stderr)
        return 1

    except OSError as e:
        print(
            f"ERROR: Network error connecting to {SMTP_HOST}:{SMTP_PORT}: {e}\n"
            "  Check your internet connection.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
