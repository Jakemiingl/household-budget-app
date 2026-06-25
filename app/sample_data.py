"""Realistic fake household data for development & demos.

Lets you exercise budgeting, goals, and chat with zero Plaid setup. Generates
~4 months of two-income-household transactions. Replaced entirely by real Plaid
data once you connect a bank.
"""
from __future__ import annotations

import sqlite3
from datetime import date

from . import budget_engine

# Plaid sign convention: positive = spending, negative = income.
ACCOUNTS = [
    # id, member, name, type, subtype, mask, current, available
    # member is always 1 (the shared household) — we don't split by person.
    ("smp_check_me", 1, "Everyday Checking", "depository", "checking", "1111", 8500.0, 8500.0),
    ("smp_check_sp", 1, "Joint Checking", "depository", "checking", "2222", 4200.0, 4200.0),
    ("smp_savings", 1, "High-Yield Savings", "depository", "savings", "3333", 22000.0, 22000.0),
    ("smp_credit", 1, "Rewards Credit Card", "credit", "credit card", "4444", 1850.0, None),
]

# Recurring monthly items: (day_of_month, account, name, merchant, amount)
MONTHLY = [
    (1, "smp_check_me", "Payroll - Acme Corp", "Acme Corp", -3300.0),
    (15, "smp_check_me", "Payroll - Acme Corp", "Acme Corp", -3300.0),
    (1, "smp_check_sp", "Payroll - Globex", "Globex", -2700.0),
    (15, "smp_check_sp", "Payroll - Globex", "Globex", -2700.0),
    (2, "smp_check_me", "Rent Payment", "Maple Apartments", 2200.0),
    (5, "smp_check_me", "City Electric Utility", "City Electric", 140.0),
    (5, "smp_check_me", "Comcast Internet", "Comcast", 85.0),
    (7, "smp_credit", "Netflix", "Netflix", 22.99),
    (9, "smp_credit", "Spotify", "Spotify", 16.99),
    (12, "smp_check_me", "Car Insurance", "Geico Insurance", 165.0),
    (20, "smp_credit", "Shell Gas", "Shell", 58.0),
    (25, "smp_credit", "Shell Gas", "Shell", 61.0),
]

# Weekly-ish variable spending: (day_of_month, account, name, merchant, amount)
VARIABLE = [
    (3, "smp_credit", "Whole Foods Market", "Whole Foods", 132.40),
    (6, "smp_credit", "Doordash", "Doordash", 47.25),
    (10, "smp_credit", "Trader Joe's", "Trader Joe's", 96.10),
    (13, "smp_credit", "Restaurant - Bella Italia", "Bella Italia", 88.00),
    (17, "smp_credit", "Whole Foods Market", "Whole Foods", 121.75),
    (19, "smp_credit", "Amazon", "Amazon", 64.30),
    (22, "smp_credit", "Trader Joe's", "Trader Joe's", 103.55),
    (24, "smp_credit", "Uber Eats", "Uber Eats", 39.80),
    (27, "smp_credit", "Target", "Target", 78.20),
    (28, "smp_credit", "Doordash", "Doordash", 52.10),
]

GOALS = [
    # name, target, current, target_date, priority
    ("Emergency Fund (6 mo)", 30000.0, 22000.0, "2026-12-31", 1),
    ("House Down Payment", 80000.0, 15000.0, "2028-06-30", 2),
    ("Dream Vacation", 8000.0, 1200.0, "2027-03-31", 3),
]


def _months_back(n: int) -> list[tuple[int, int]]:
    """Return (year, month) for the current month and the previous n-1 months."""
    today = date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


def _safe_day(year: int, month: int, day: int) -> date:
    # Clamp day to month length (e.g., no Feb 30).
    for d in (day, 28, 27):
        try:
            return date(year, month, min(day, d))
        except ValueError:
            continue
    return date(year, month, 28)


def load(conn: sqlite3.Connection, months: int = 4) -> dict:
    """Wipe financial data and load a fresh sample household."""
    conn.execute("DELETE FROM transactions")
    conn.execute("DELETE FROM accounts")
    conn.execute("DELETE FROM plaid_items")
    conn.execute("DELETE FROM goals")

    for a in ACCOUNTS:
        conn.execute(
            """INSERT INTO accounts(id,member_id,name,type,subtype,mask,
                   current_balance,available_balance,currency)
               VALUES(?,?,?,?,?,?,?,?, 'USD')""",
            a,
        )

    today = date.today()
    n = 0
    for (year, month) in _months_back(months):
        for (day, acct, name, merchant, amount) in MONTHLY + VARIABLE:
            d = _safe_day(year, month, day)
            if d > today:
                continue  # don't post future-dated transactions
            tid = f"smp_{acct}_{d.isoformat()}_{n}"
            cid = budget_engine.categorize(conn, name, merchant, amount)
            conn.execute(
                """INSERT INTO transactions(id,account_id,date,name,merchant_name,
                       amount,currency,category_id,pending)
                   VALUES(?,?,?,?,?,?, 'USD', ?, 0)""",
                (tid, acct, d.isoformat(), name, merchant, amount, cid),
            )
            n += 1

    for g in GOALS:
        conn.execute(
            """INSERT INTO goals(name,target_amount,current_amount,target_date,priority)
               VALUES(?,?,?,?,?)""",
            g,
        )

    return {"accounts": len(ACCOUNTS), "transactions": n, "goals": len(GOALS)}
