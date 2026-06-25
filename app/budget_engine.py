"""Budgeting & analysis. All deterministic SQL/Python — no LLM, no cost.

Sign convention (from Plaid): transaction amount POSITIVE = money out
(spending), NEGATIVE = money in (income/refund). We normalize to friendly
income/expense numbers here.
"""
from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta


def _add_months_date(start: date, months: int) -> str:
    """Approximate calendar date `months` out (30.44-day months). ISO string."""
    return (start + timedelta(days=round(months * 30.44))).isoformat()

# Manual accounts (including physical assets like vehicles/RVs) don't auto-update
# the way Plaid accounts do, so their balances drift. Flag any manual account not
# touched in this many days so net worth doesn't quietly go stale.
STALE_AFTER_DAYS = 90

# Strip ALL non-alphanumerics (spaces included) from BOTH the keyword and the
# txn text before matching, so a keyword matches regardless of spacing or
# punctuation: "t mobile" matches "T-Mobile", "google workspace" matches the
# concatenated "GOOGLEWORKSPACE", "att" matches "AT&T". Intentionally permissive
# (word boundaries are ignored) — fine for a self-reviewed personal rule set;
# the "N transaction(s) updated" count is the sanity check against over-matching.
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def norm_text(s: str | None) -> str:
    return _NON_ALNUM.sub("", (s or "").lower())


def txn_haystack(name: str | None, merchant: str | None) -> str:
    return norm_text(f"{name or ''} {merchant or ''}")


def rule_matches(pattern: str | None, name: str | None, merchant: str | None) -> bool:
    p = norm_text(pattern)
    return bool(p) and p in txn_haystack(name, merchant)


# Map Plaid's personal_finance_category to our category NAMES. PFC arrives as a
# "detailed" string (e.g. GENERAL_MERCHANDISE_OTHER_GENERAL_MERCHANDISE) whose
# prefix is the primary bucket. Used only as a HINT (pre-fill in the
# uncategorized queue, and to flag possible rule mismatches) — never to override
# a user's keyword rule. Returns None when there's no confident mapping.
_PFC_PRIMARY_MAP = {
    "INCOME": "Income",
    "TRANSFER_IN": "Transfer",
    "TRANSFER_OUT": "Transfer",
    "LOAN_PAYMENTS": "Debt Payment",
    "ENTERTAINMENT": "Entertainment",
    "FOOD_AND_DRINK": "Dining & Takeout",
    "GENERAL_MERCHANDISE": "Shopping",
    "HOME_IMPROVEMENT": "Shopping",
    "MEDICAL": "Health",
    "PERSONAL_CARE": "Health",
    "TRANSPORTATION": "Transportation",
    "TRAVEL": "Travel",
    "RENT_AND_UTILITIES": "Utilities",
}


def pfc_to_category_name(detailed: str | None) -> str | None:
    if not detailed:
        return None
    d = detailed.upper()
    # Detailed-level refinements that beat the primary bucket.
    if "GROCERIES" in d:
        return "Groceries"
    if d.endswith("_RENT") or "_RENT_" in d:
        return "Housing & Rent"
    if "INSURANCE" in d:
        return "Insurance"
    for primary, cat in _PFC_PRIMARY_MAP.items():
        if d.startswith(primary):
            return cat
    return None


def direction_matches(direction: str | None, amount: float) -> bool:
    """Whether a rule's direction applies to a transaction's sign.

    'in'  = money in   (Plaid amount < 0, e.g. a Mercury paycheck -> Income)
    'out' = money out  (Plaid amount > 0, e.g. a Mercury card payment -> Debt)
    'any' (or unset)   = both signs.
    """
    if direction == "in":
        return amount < 0
    if direction == "out":
        return amount > 0
    return True


# ---------------------------------------------------------------- categorization
def categorize_with_rule(conn: sqlite3.Connection, name: str | None,
                         merchant: str | None, amount: float) -> tuple[int, int | None]:
    """Pick (category_id, rule_id) for a transaction using keyword rules.

    rule_id is the id of the rule that matched, or None when nothing matched and
    we fell back to 'Income' (money-in) / 'Uncategorized' (money-out).
    """
    haystack = txn_haystack(name, merchant)
    rules = conn.execute(
        "SELECT id, pattern, category_id, direction FROM category_rules "
        "ORDER BY priority ASC"
    ).fetchall()
    for r in rules:
        p = norm_text(r["pattern"])
        if p and p in haystack and direction_matches(r["direction"], amount):
            return r["category_id"], r["id"]

    fallback = "Income" if amount < 0 else "Uncategorized"
    row = conn.execute(
        "SELECT id FROM categories WHERE name = ?", (fallback,)
    ).fetchone()
    return row["id"], None


def categorize(conn: sqlite3.Connection, name: str | None,
               merchant: str | None, amount: float) -> int:
    """Back-compat helper: just the category id (see categorize_with_rule)."""
    return categorize_with_rule(conn, name, merchant, amount)[0]


def recategorize_all(conn: sqlite3.Connection) -> int:
    """Re-apply rules to every transaction (after editing rules). Returns count."""
    txns = conn.execute(
        "SELECT id, name, merchant_name, amount FROM transactions"
    ).fetchall()
    for t in txns:
        cid, rid = categorize_with_rule(conn, t["name"], t["merchant_name"], t["amount"])
        conn.execute(
            "UPDATE transactions SET category_id = ?, category_rule_id = ? WHERE id = ?",
            (cid, rid, t["id"]),
        )
    return len(txns)


# ---------------------------------------------------------------- net worth
def net_worth(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT type, current_balance FROM accounts").fetchall()
    assets = liabilities = real_assets = 0.0
    for r in rows:
        bal = r["current_balance"] or 0.0
        if r["type"] in ("credit", "loan"):
            liabilities += bal
        else:
            assets += bal
            if r["type"] == "asset":   # physical assets (vehicles, RV, property)
                real_assets += bal
    return {
        "assets": round(assets, 2),
        "liabilities": round(liabilities, 2),
        "real_assets": round(real_assets, 2),  # subset of assets: things you own
        "net_worth": round(assets - liabilities, 2),
    }


def accounts_summary(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT a.id, a.name, a.type, a.subtype, a.mask, a.current_balance,
                  a.available_balance, a.currency, a.updated_at, a.linked_account_id,
                  a.interest_rate, a.monthly_payment,
                  (a.item_id IS NULL) AS is_manual, m.name AS owner
           FROM accounts a LEFT JOIN members m ON a.member_id = m.id
           ORDER BY a.type, a.name"""
    ).fetchall()
    by_id = {r["id"]: r for r in rows}
    today = date.today()
    out = []
    for r in rows:
        d = dict(r)
        # Estimated interest this month on a liability = balance × APR/12.
        d["monthly_interest"] = None
        if d["type"] in ("credit", "loan") and d["interest_rate"]:
            d["monthly_interest"] = round(
                (d["current_balance"] or 0.0) * d["interest_rate"] / 1200.0, 2)
        # Staleness: manual accounts have no auto-update, so flag old ones.
        d["is_stale"], d["stale_days"] = False, None
        if d["is_manual"] and d["updated_at"]:
            try:
                updated = datetime.strptime(d["updated_at"][:10], "%Y-%m-%d").date()
                d["stale_days"] = (today - updated).days
                d["is_stale"] = d["stale_days"] > STALE_AFTER_DAYS
            except ValueError:
                pass
        # Equity: for an asset linked to its financing loan, value − amount owed.
        d["linked_name"], d["equity"] = None, None
        linked = by_id.get(d["linked_account_id"]) if d["linked_account_id"] else None
        if d["type"] == "asset" and linked is not None:
            d["linked_name"] = linked["name"]
            d["equity"] = round((d["current_balance"] or 0.0)
                                - (linked["current_balance"] or 0.0), 2)
        out.append(d)
    return out


def _amortize(balance: float, apr: float | None, payment: float | None) -> dict | None:
    """Simulate paying `payment`/month against `balance` at `apr`%, month by month.

    Returns the realistic payoff time and TRUE interest (interest shrinks as the
    balance falls — so this is not balance×APR×years). Returns None if no payment is
    set (can't project paydown). `interest_next_12mo` is summed over the next 12
    months (or until payoff, whichever comes first), so it's accurate even when the
    debt clears partway through the year. If the payment can't cover the monthly
    interest the balance never falls → never_pays_off (still report the 12-mo bleed).
    """
    if not payment or payment <= 0:
        return None
    r = (apr or 0.0) / 1200.0
    bal = balance
    never = r > 0 and payment <= bal * r
    total_interest = 0.0
    interest_12 = 0.0
    months = 0
    MAX_MONTHS = 1200  # 100-year backstop so a near-miss payment can't loop forever
    while bal > 1e-9 and months < MAX_MONTHS:
        interest = bal * r
        total_interest += interest
        if months < 12:
            interest_12 += interest
        bal = bal + interest - payment
        months += 1
        if never and months >= 12:
            break  # diverging; the 12-month figure is all that's meaningful
    return {
        "never_pays_off": never,
        "payoff_months": None if never else months,
        "payoff_total_interest": None if never else round(total_interest, 2),
        "interest_next_12mo": round(interest_12, 2),
    }


def debt_payoff_plan(conn: sqlite3.Connection) -> dict:
    """Rank liabilities for payoff using the avalanche method (highest APR first).

    Avalanche minimizes total interest paid. Debts with a known APR sort ahead of
    those without (we can't rank an unknown rate, so they fall to the bottom with a
    flag). `monthly_interest` is the current snapshot (balance × APR/12). When a
    `monthly_payment` is set we additionally amortize real paydown → payoff date and
    TRUE interest (12-month + to-clear), instead of assuming a static balance.
    """
    rows = conn.execute(
        """SELECT id, name, type, subtype, mask, current_balance, interest_rate,
                  monthly_payment
           FROM accounts WHERE type IN ('credit','loan')"""
    ).fetchall()
    today = date.today()
    debts = []
    for r in rows:
        bal = r["current_balance"] or 0.0
        if bal <= 0:
            continue  # nothing owed → not part of the payoff plan
        apr = r["interest_rate"]
        payment = r["monthly_payment"]
        d = {
            "id": r["id"], "name": r["name"], "type": r["type"],
            "subtype": r["subtype"], "mask": r["mask"],
            "balance": round(bal, 2),
            "apr": apr,
            "has_rate": apr is not None,
            "monthly_payment": payment,
            "monthly_interest": round(bal * apr / 1200.0, 2) if apr else None,
            # amortized fields (None until a payment is set)
            "payoff_months": None, "payoff_date": None,
            "payoff_total_interest": None, "interest_next_12mo": None,
            "never_pays_off": None,
        }
        am = _amortize(bal, apr, payment)
        if am:
            d.update({
                "never_pays_off": am["never_pays_off"],
                "payoff_months": am["payoff_months"],
                "payoff_total_interest": am["payoff_total_interest"],
                "interest_next_12mo": am["interest_next_12mo"],
                "payoff_date": (_add_months_date(today, am["payoff_months"])
                                if am["payoff_months"] is not None else None),
            })
        debts.append(d)
    # Avalanche order: known rates by APR desc, then unknown-rate debts last.
    debts.sort(key=lambda d: (d["apr"] is None, -(d["apr"] or 0.0), -d["balance"]))
    for i, d in enumerate(debts):
        d["payoff_order"] = i + 1
    total_debt = sum(d["balance"] for d in debts)
    total_monthly_interest = sum(d["monthly_interest"] or 0.0 for d in debts)
    # TRUE next-12-month interest, summed only over debts we can project (payment set).
    projected = [d for d in debts if d["interest_next_12mo"] is not None]
    total_interest_12mo = sum(d["interest_next_12mo"] for d in projected)
    return {
        "debts": debts,
        "total_debt": round(total_debt, 2),
        "total_monthly_interest": round(total_monthly_interest, 2),  # snapshot, this month
        "total_interest_next_12mo": round(total_interest_12mo, 2),   # amortized, accurate
        "projected_count": len(projected),
        "missing_rates": sum(1 for d in debts if not d["has_rate"]),
        "missing_payments": sum(1 for d in debts if not d["monthly_payment"]),
        "method": "avalanche",
    }


# ---------------------------------------------------------------- monthly view
def _this_month() -> str:
    return date.today().strftime("%Y-%m")


def monthly_summary(conn: sqlite3.Connection, month: str | None = None) -> dict:
    month = month or _this_month()
    # Net all amounts within a category (sign-adjusted by kind), so a refund
    # (money-in) nets down its expense category and a returned deposit (money-out)
    # nets down income — instead of vanishing from both totals.
    income = conn.execute(
        """SELECT COALESCE(-SUM(t.amount), 0) FROM transactions t
           JOIN categories c ON t.category_id = c.id
           WHERE substr(t.date,1,7)=? AND c.kind='income'""",
        (month,),
    ).fetchone()[0]
    expenses = conn.execute(
        """SELECT COALESCE(SUM(t.amount), 0) FROM transactions t
           JOIN categories c ON t.category_id = c.id
           WHERE substr(t.date,1,7)=? AND c.kind='expense'""",
        (month,),
    ).fetchone()[0]
    by_category = conn.execute(
        """SELECT c.name AS category, c.kind, ROUND(SUM(t.amount),2) AS total,
                  COUNT(*) AS n
           FROM transactions t JOIN categories c ON t.category_id = c.id
           WHERE substr(t.date,1,7)=? AND c.kind='expense'
           GROUP BY c.id ORDER BY total DESC""",
        (month,),
    ).fetchall()
    return {
        "month": month,
        "income": round(income, 2),
        "expenses": round(expenses, 2),
        "net": round(income - expenses, 2),
        "by_category": [dict(r) for r in by_category],
    }


def budget_vs_actual(conn: sqlite3.Connection, month: str | None = None) -> list[dict]:
    month = month or _this_month()
    rows = conn.execute(
        """SELECT c.id AS category_id, c.name AS category, b.monthly_limit AS limit_amt,
                  COALESCE((SELECT SUM(t.amount) FROM transactions t
                            WHERE t.category_id=c.id
                            AND substr(t.date,1,7)=?), 0) AS actual
           FROM budgets b JOIN categories c ON b.category_id=c.id
           ORDER BY c.name""",
        (month,),
    ).fetchall()
    out = []
    for r in rows:
        limit_amt = r["limit_amt"] or 0.0
        actual = r["actual"] or 0.0
        out.append({
            "category_id": r["category_id"],
            "category": r["category"],
            "limit": round(limit_amt, 2),
            "actual": round(actual, 2),
            "remaining": round(limit_amt - actual, 2),
            "pct": round(100 * actual / limit_amt, 1) if limit_amt else None,
            "over": actual > limit_amt,
        })
    return out


def cash_flow(conn: sqlite3.Connection, months: int = 6) -> list[dict]:
    """Income/expense/net for each of the last `months` months."""
    rows = conn.execute(
        """SELECT substr(t.date,1,7) AS month,
                  ROUND(COALESCE(-SUM(CASE WHEN c.kind='income'
                        THEN t.amount END),0),2) AS income,
                  ROUND(COALESCE(SUM(CASE WHEN c.kind='expense'
                        THEN t.amount END),0),2) AS expenses
           FROM transactions t JOIN categories c ON t.category_id=c.id
           GROUP BY month ORDER BY month DESC LIMIT ?""",
        (months,),
    ).fetchall()
    out = []
    for r in rows:
        out.append({
            "month": r["month"],
            "income": r["income"],
            "expenses": r["expenses"],
            "net": round(r["income"] - r["expenses"], 2),
        })
    return list(reversed(out))


def category_averages(conn: sqlite3.Connection, months: int = 3) -> list[dict]:
    """Average monthly spend per expense category over recent complete months.
    Feeds AI budget suggestions so they're grounded in real history.
    """
    flow = cash_flow(conn, months=months + 1)
    month_keys = [f["month"] for f in flow]
    if len(month_keys) > 1 and month_keys[-1] == _this_month():
        month_keys = month_keys[:-1]
    month_keys = month_keys[-months:]
    if not month_keys:
        return []
    placeholders = ",".join("?" * len(month_keys))
    rows = conn.execute(
        f"""SELECT c.name AS category,
                   ROUND(SUM(t.amount) / ?, 2) AS avg_monthly,
                   ROUND(SUM(t.amount), 2) AS total
            FROM transactions t JOIN categories c ON t.category_id = c.id
            WHERE c.kind='expense'
                  AND substr(t.date,1,7) IN ({placeholders})
            GROUP BY c.id ORDER BY avg_monthly DESC""",
        [len(month_keys), *month_keys],
    ).fetchall()
    return [dict(r) for r in rows]


def average_monthly_surplus(conn: sqlite3.Connection, months: int = 3) -> float:
    """Average (income - expenses) over recent complete-ish months.

    This is the engine of goal projections: how much the household can save
    per month, on average, based on real cash flow.
    """
    flow = cash_flow(conn, months=months + 1)
    # Drop the current (partial) month if we have history beyond it.
    if len(flow) > 1 and flow[-1]["month"] == _this_month():
        flow = flow[:-1]
    flow = flow[-months:]
    if not flow:
        return 0.0
    return round(sum(f["net"] for f in flow) / len(flow), 2)
