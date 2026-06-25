"""Scheduled chart reports → Telegram.

Run by Windows Task Scheduler; does NOT need the web server running. Generates one
chart and sends it to every allowed Telegram chat ID, then exits. Also records a
net-worth snapshot on every run so history keeps accruing.

Usage:
    python -m app.reports goals      # Monday: goal progress
    python -m app.reports cashflow   # 1st Wednesday: cash flow (excludes current month)
    python -m app.reports networth   # last Friday: net worth over time
    python -m app.reports snapshot   # just record a net-worth point (no send)
    python -m app.reports all        # send all three (handy for testing)
"""
from __future__ import annotations

import sys
from datetime import date, datetime

from . import budget_engine, charts, telegram_bot
from .config import DATA_DIR, settings
from .db import db_cursor

# Captions carry emoji; the Windows console codepage (cp1252) can't encode them,
# so make stdout/stderr tolerant rather than crashing AFTER a chart was sent.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_LOG = DATA_DIR / "reports.log"


def _log(msg: str) -> None:
    """Print and append to data/reports.log, so scheduled runs leave a trail."""
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} [reports] {msg}"
    try:
        print(line)
    except Exception:
        pass
    try:
        with open(_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _send(png: bytes, caption: str) -> None:
    token = settings.telegram_bot_token
    ids = settings.telegram_allowed_set
    if not token or not ids:
        _log("no Telegram token / allowed chat IDs — nothing sent.")
        return
    n = telegram_bot.send_photo_sync(token, ids, png, caption)
    _log(f"sent '{caption}' to {n}/{len(ids)} chat(s).")


def run(kind: str) -> int:
    today = date.today()
    with db_cursor() as conn:
        budget_engine.record_snapshot(conn)  # always capture a point
        if kind == "snapshot":
            _log("net-worth snapshot recorded.")
        elif kind == "goals":
            _send(charts.goals_chart(conn),
                  f"🎯 Goal progress — {today:%b %d, %Y}")
        elif kind == "cashflow":
            _send(charts.cashflow_chart(conn, months=6, include_current=False),
                  f"📊 Monthly cash flow (through last month) — {today:%b %Y}")
        elif kind == "networth":
            _send(charts.networth_chart(conn),
                  f"💰 Net worth over time — {today:%b %d, %Y}")
        elif kind == "all":
            _send(charts.goals_chart(conn), f"🎯 Goal progress — {today:%b %d, %Y}")
            _send(charts.cashflow_chart(conn, months=6, include_current=False),
                  f"📊 Monthly cash flow (through last month) — {today:%b %Y}")
            _send(charts.networth_chart(conn),
                  f"💰 Net worth over time — {today:%b %d, %Y}")
        else:
            _log(f"unknown report kind: {kind!r}")
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1] if len(sys.argv) > 1 else "all"))
