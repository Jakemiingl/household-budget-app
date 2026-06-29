"""Financial goals & timeline projections.

Model (intentionally simple and explainable):
- The household saves an average monthly *surplus* (income - expenses), computed
  from real transaction history by budget_engine.average_monthly_surplus.
- Goals are funded in PRIORITY ORDER (a waterfall): the highest-priority goal
  gets the full surplus until funded, then the next, and so on. This makes the
  effect of any change easy to reason about and explain.
- A one-time purchase of $X is modeled as consuming surplus that would otherwise
  fund goals, so it pushes every not-yet-funded goal back by X / surplus months.
  (Conservative "does this slow us down?" view.)
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta

from . import budget_engine

_AVG_DAYS_PER_MONTH = 30.44


def _add_months(start: date, months: float) -> date:
    return start + timedelta(days=round(months * _AVG_DAYS_PER_MONTH))


def _payoff_months(balance: float, monthly_rate: float, payment: float) -> float:
    """Months to clear `balance` at `monthly_rate` paying `payment`/month.

    Standard amortization. With no interest this is just balance/payment. If the
    payment can't cover the first month's interest, the balance never falls →
    returns inf (caller renders this as "never at the current rate").
    """
    if balance <= 0:
        return 0.0
    if payment <= 0:
        return math.inf
    if monthly_rate <= 0:
        return balance / payment
    if payment <= balance * monthly_rate:
        return math.inf
    return -math.log(1 - (balance * monthly_rate) / payment) / math.log(1 + monthly_rate)


def _goals(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """SELECT id, name, target_amount, current_amount, target_date, priority,
                  completed_at
           FROM goals ORDER BY priority ASC, target_date ASC"""
    ).fetchall()
    return [dict(r) for r in rows]


_LIABILITY_TYPES = ("credit", "loan")


def bound_state(conn: sqlite3.Connection, goal_id: int,
                stored_target: float) -> dict | None:
    """Derive a goal's progress from its bound accounts, or None if unbound.

    All-liability accounts → 'payoff' mode: progress = how much the debt has been
    paid down since the goal was set (start snapshot − current balance), and the
    target is the debt that existed at the start. Otherwise → 'savings' mode:
    progress = the current combined balance toward the user's target.
    """
    rows = conn.execute(
        """SELECT a.id, COALESCE(a.custom_name, a.name) AS name, a.type,
                  a.current_balance, a.interest_rate, ga.start_balance
           FROM goal_accounts ga JOIN accounts a ON ga.account_id = a.id
           WHERE ga.goal_id = ?""",
        (goal_id,),
    ).fetchall()
    if not rows:
        return None
    accounts = [dict(r) for r in rows]
    cur_bal = sum((a["current_balance"] or 0.0) for a in accounts)
    start_bal = sum((a["start_balance"] or 0.0) for a in accounts)
    payoff = all(a["type"] in _LIABILITY_TYPES for a in accounts)
    # Blended APR across the bound debts, weighted by current balance. Accounts
    # with no rate contribute 0% (so the projection just under-counts their
    # interest rather than failing) — surfaced as apr_known=False.
    blended_apr = 0.0
    apr_known = False
    if payoff and cur_bal > 0:
        blended_apr = sum((a["current_balance"] or 0.0) * (a["interest_rate"] or 0.0)
                          for a in accounts) / cur_bal
        apr_known = any(a["interest_rate"] for a in accounts)
    if payoff:
        mode = "payoff"
        target = start_bal                       # the debt to clear
        current = max(0.0, start_bal - cur_bal)  # amount paid down so far
        complete = cur_bal <= 0
    else:
        mode = "savings"
        target = stored_target
        current = cur_bal
        complete = target > 0 and current >= target
    return {
        "mode": mode,
        "target_amount": round(target, 2),
        "current_amount": round(current, 2),
        "apr": round(blended_apr, 2),
        "apr_known": apr_known,
        "monthly_rate": blended_apr / 1200.0,
        "complete": complete,
        "accounts": [
            {"id": a["id"], "name": a["name"], "type": a["type"],
             "current_balance": a["current_balance"],
             "start_balance": a["start_balance"]}
            for a in accounts
        ],
    }


def project(conn: sqlite3.Connection, surplus: float | None = None,
            upfront_cost: float = 0.0) -> dict:
    """Project completion for every goal as a priority waterfall.

    `upfront_cost` is a one-time spend taken out of surplus before goals resume.
    """
    if surplus is None:
        surplus = budget_engine.average_monthly_surplus(conn)
    goals = _goals(conn)
    today = date.today()

    # Months already "used up" by the upfront purchase.
    cumulative = (upfront_cost / surplus) if (surplus > 0 and upfront_cost) else 0.0

    results = []
    for g in goals:
        # Account-bound goals derive their target/progress from live balances.
        state = bound_state(conn, g["id"], g["target_amount"])
        if state:
            target_amount = state["target_amount"]
            current_amount = state["current_amount"]
        else:
            target_amount = g["target_amount"]
            current_amount = g["current_amount"]

        # Completion is sticky: once reached, it stays done (paying a card off and
        # then reusing it doesn't un-finish the goal).
        reached = bool(state["complete"]) if state else (
            target_amount > 0 and current_amount >= target_amount)
        completed_at = g["completed_at"]
        if reached and not completed_at:
            completed_at = today.isoformat()
            conn.execute("UPDATE goals SET completed_at=? WHERE id=?",
                         (completed_at, g["id"]))
        complete = bool(completed_at)

        remaining = max(0.0, target_amount - current_amount)
        # For a debt-payoff goal with a known APR, the balance keeps accruing
        # interest while we pay it down, so model it as amortization instead of a
        # flat remaining/surplus. (Simplification: interest during the months this
        # goal is still waiting behind higher-priority goals isn't compounded.)
        monthly_rate = state["monthly_rate"] if (state and state["mode"] == "payoff") else 0.0
        interest_to_payoff = None
        if complete:
            months_for = 0.0
        elif surplus <= 0:
            months_for = math.inf
        elif monthly_rate > 0:
            months_for = _payoff_months(remaining, monthly_rate, surplus)
            if math.isfinite(months_for):
                interest_to_payoff = round(surplus * months_for - remaining, 2)
        else:
            months_for = remaining / surplus
        finish = cumulative + months_for
        projected_date = _add_months(today, finish) if math.isfinite(finish) else None

        on_track = None
        if g["target_date"] and projected_date and not complete:
            try:
                target = date.fromisoformat(g["target_date"])
                on_track = projected_date <= target
            except ValueError:
                on_track = None

        results.append({
            "id": g["id"],
            "name": g["name"],
            "target_amount": round(target_amount, 2),
            "current_amount": round(current_amount, 2),
            "remaining": round(remaining, 2),
            "target_date": g["target_date"],
            "priority": g["priority"],
            "complete": complete,
            "completed_at": completed_at,
            "mode": state["mode"] if state else "manual",
            "apr": (state["apr"] if state and state["mode"] == "payoff" else None),
            "apr_known": (state["apr_known"] if state and state["mode"] == "payoff" else None),
            "interest_to_payoff": interest_to_payoff,
            "accounts": state["accounts"] if state else [],
            "months_to_complete": (0.0 if complete else
                                   (None if not math.isfinite(finish)
                                    else round(finish, 1))),
            "projected_date": (completed_at if complete else
                               (projected_date.isoformat() if projected_date else None)),
            "on_track": on_track,
        })
        # Completed goals don't consume surplus; only active goals push the
        # waterfall start date for the goals behind them.
        if not complete and math.isfinite(months_for):
            cumulative = finish

    return {
        "monthly_surplus": round(surplus, 2),
        "upfront_cost": round(upfront_cost, 2),
        "goals": results,
    }


def record_goal_snapshots(conn: sqlite3.Connection) -> None:
    """Snapshot today's progress for every goal (one row per goal per day).

    Idempotent — re-running on the same day updates the row. Builds the history the
    "progress over time" line chart plots.
    """
    for g in project(conn)["goals"]:
        conn.execute(
            """INSERT INTO goal_snapshots
                   (snapshot_date, goal_id, name, current_amount, target_amount)
               VALUES (date('now'), ?, ?, ?, ?)
               ON CONFLICT(snapshot_date, goal_id) DO UPDATE SET
                   name=excluded.name, current_amount=excluded.current_amount,
                   target_amount=excluded.target_amount""",
            (g["id"], g["name"], g["current_amount"], g["target_amount"]),
        )


def goal_history(conn: sqlite3.Connection) -> list[dict]:
    """Per-goal progress series for the line chart: % toward target over time."""
    rows = conn.execute(
        """SELECT snapshot_date, goal_id, name, current_amount, target_amount
           FROM goal_snapshots ORDER BY goal_id, snapshot_date"""
    ).fetchall()
    by_goal: dict[int, dict] = {}
    for r in rows:
        g = by_goal.setdefault(r["goal_id"], {"goal_id": r["goal_id"],
                                              "name": r["name"], "points": []})
        g["name"] = r["name"]  # keep the latest label
        pct = (r["current_amount"] / r["target_amount"] * 100.0
               if r["target_amount"] > 0 else 0.0)
        g["points"].append({"date": r["snapshot_date"],
                            "pct": max(0.0, min(100.0, pct)),
                            "current": r["current_amount"],
                            "target": r["target_amount"]})
    return list(by_goal.values())


def simulate_purchase(conn: sqlite3.Connection, amount: float) -> dict:
    """Compare goal timelines with vs. without a one-time purchase of `amount`.

    Returns per-goal delay in months plus the affordability verdict.
    """
    surplus = budget_engine.average_monthly_surplus(conn)
    nw = budget_engine.net_worth(conn)

    base = project(conn, surplus=surplus, upfront_cost=0.0)
    after = project(conn, surplus=surplus, upfront_cost=amount)

    impacts = []
    for b, a in zip(base["goals"], after["goals"]):
        delay = None
        if b["months_to_complete"] is not None and a["months_to_complete"] is not None:
            delay = round(a["months_to_complete"] - b["months_to_complete"], 1)
        impacts.append({
            "name": b["name"],
            "baseline_date": b["projected_date"],
            "new_date": a["projected_date"],
            "delay_months": delay,
            "was_on_track": b["on_track"],
            "still_on_track": a["on_track"],
        })

    # Affordability heuristic: can be paid from assets, and surplus is positive.
    affordable_from_cash = amount <= nw["assets"]
    months_of_surplus = (round(amount / surplus, 1) if surplus > 0 else None)

    return {
        "amount": round(amount, 2),
        "monthly_surplus": round(surplus, 2),
        "assets": nw["assets"],
        "affordable_from_assets": affordable_from_cash,
        "equivalent_months_of_surplus": months_of_surplus,
        "goal_impacts": impacts,
    }
