"""Recurring charge / subscription detection. Pure analysis, no LLM.

We group spending transactions by a normalized merchant key, then look for a
regular cadence (weekly / biweekly / monthly / yearly) across the dates. For each
detected series we report the typical amount, next expected date, and a
monthly-equivalent cost so it can feed budgeting.
"""
from __future__ import annotations

import re
import sqlite3
import statistics
from datetime import date, timedelta

_AVG_DAYS_PER_MONTH = 30.44

# name, ideal gap (days), accepted low, accepted high
_CADENCES = [
    ("weekly", 7, 5, 9),
    ("biweekly", 14, 11, 18),
    ("monthly", 30, 24, 37),
    ("yearly", 365, 330, 400),
]


def _key(name: str | None, merchant: str | None) -> str:
    """Normalize to a merchant key: lowercase, drop digits/punctuation, first
    few words — so "SQ *BLUE BOTTLE 0423" and "Blue Bottle" group together."""
    base = (merchant or name or "").lower()
    base = re.sub(r"[^a-z ]", " ", base)
    base = re.sub(r"\s+", " ", base).strip()
    return " ".join(base.split()[:3])


def _classify(median_gap: float) -> tuple[str | None, int | None]:
    for name, ideal, lo, hi in _CADENCES:
        if lo <= median_gap <= hi:
            return name, ideal
    return None, None


def detect(conn: sqlite3.Connection, min_occurrences: int = 3) -> dict:
    rows = conn.execute(
        """SELECT t.date, t.name, t.merchant_name, t.amount, c.name AS category
           FROM transactions t LEFT JOIN categories c ON t.category_id = c.id
           WHERE t.amount > 0
           ORDER BY t.date"""
    ).fetchall()

    groups: dict[str, list] = {}
    for r in rows:
        k = _key(r["name"], r["merchant_name"])
        if k:
            groups.setdefault(k, []).append(r)

    # Manually dismissed series: {merchant_key: dismissed_at date}. A series is
    # hidden only while its latest charge is on/before the dismissal date — a
    # newer charge makes it resurface (e.g., a "cancelled" sub that bills again).
    dismissed = {
        row["merchant_key"]: date.fromisoformat(row["dismissed_at"])
        for row in conn.execute(
            "SELECT merchant_key, dismissed_at FROM recurring_dismissed"
        ).fetchall()
    }

    today = date.today()
    items: list[dict] = []
    for key, txns in groups.items():
        if len(txns) < min_occurrences:
            continue
        dates = sorted(date.fromisoformat(t["date"]) for t in txns)
        if key in dismissed and dates[-1] <= dismissed[key]:
            continue  # dismissed and nothing new since
        gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
        gaps = [g for g in gaps if g > 0]
        if not gaps:
            continue

        median_gap = statistics.median(gaps)
        cadence, ideal = _classify(median_gap)
        if not cadence:
            continue

        # Require most gaps to sit near the ideal cadence (rejects noise).
        tol = max(4, ideal * 0.4)
        regular = sum(1 for g in gaps if abs(g - ideal) <= tol)
        if regular < len(gaps) * 0.6:
            continue

        amounts = [t["amount"] for t in txns]
        med_amt = round(statistics.median(amounts), 2)
        varies = (max(amounts) - min(amounts)) > max(1.0, 0.15 * med_amt)
        last_date = dates[-1]
        next_expected = last_date + timedelta(days=ideal)
        # "active" if the most recent charge is within ~1.5 cycles of today.
        active = (today - last_date).days <= ideal * 1.5 + 3

        items.append({
            "key": key,
            "merchant": txns[-1]["merchant_name"] or txns[-1]["name"],
            "category": txns[-1]["category"],
            "cadence": cadence,
            "amount": med_amt,
            "amount_varies": varies,
            "occurrences": len(txns),
            "last_date": last_date.isoformat(),
            "next_expected": next_expected.isoformat(),
            "monthly_equivalent": round(med_amt * (_AVG_DAYS_PER_MONTH / ideal), 2),
            "active": active,
        })

    items.sort(key=lambda x: (not x["active"], -x["monthly_equivalent"]))
    monthly_total = round(
        sum(i["monthly_equivalent"] for i in items if i["active"]), 2
    )
    return {
        "recurring": items,
        "count": len(items),
        "active_count": sum(1 for i in items if i["active"]),
        "monthly_total": monthly_total,
        "annual_total": round(monthly_total * 12, 2),
    }
