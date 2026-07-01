"""Budgets, monthly summary, and cash-flow trend."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import budget_engine
from ..db import db_cursor

router = APIRouter()


class BudgetBody(BaseModel):
    monthly_limit: float


@router.get("")
def budget_status(month: str | None = None):
    with db_cursor() as conn:
        summary = budget_engine.monthly_summary(conn, month)
        # Headline "surplus" = actual cash in − cash out of the bank accounts this
        # month (checking/savings). This is real money-in-minus-money-out and avoids
        # the credit-card double-count (purchases hit the card, not the bank; only
        # the payment leaves cash). income/expenses stay available for context.
        cash = budget_engine.checking_cash_flow(conn, month)
        return {
            "month": summary["month"],
            "budgets": budget_engine.budget_vs_actual(conn, month),
            "surplus": cash["net"],
            "cash_in": cash["cash_in"],
            "cash_out": cash["cash_out"],
            "income": summary["income"],
            "expenses": summary["expenses"],
            # Credit-card paydown: dollars sent to Plaid cards this month + planned
            # target. A transfer (not an expense) so it doesn't double-count purchases.
            "credit_card": budget_engine.credit_card_summary(conn, month),
        }


@router.get("/category-transactions")
def category_transactions(category_id: int, month: str | None = None):
    """The individual transactions that make up a category's 'actual' for a month.

    Backs the drill-down when you click a budget row's Spent figure. The sum of
    `amount` here equals the `actual` shown in budget_vs_actual (money-in nets
    down, matching the netting used by the totals), so the numbers reconcile.
    """
    month = month or budget_engine._this_month()
    with db_cursor() as conn:
        cat = conn.execute(
            "SELECT name, kind FROM categories WHERE id=?", (category_id,)
        ).fetchone()
        if cat is None:
            raise HTTPException(404, "Category not found")
        rows = conn.execute(
            """SELECT t.id, t.date, t.name, t.merchant_name, t.amount, t.pending,
                      a.name AS account, t.category_rule_id, r.pattern AS rule_pattern
               FROM transactions t
               LEFT JOIN accounts a ON t.account_id = a.id
               LEFT JOIN category_rules r ON t.category_rule_id = r.id
               WHERE t.category_id = ? AND substr(t.date,1,7) = ?
               ORDER BY t.amount DESC, t.date DESC""",
            (category_id, month),
        ).fetchall()
        txns = [dict(r) for r in rows]
        return {
            "category_id": category_id,
            "category": cat["name"],
            "kind": cat["kind"],
            "month": month,
            "actual": round(sum(t["amount"] for t in txns), 2),
            "count": len(txns),
            "transactions": txns,
        }


@router.get("/summary")
def summary(month: str | None = None):
    with db_cursor() as conn:
        return budget_engine.monthly_summary(conn, month)


@router.get("/cash-flow")
def cash_flow(months: int = 6):
    with db_cursor() as conn:
        return {"cash_flow": budget_engine.cash_flow(conn, months)}


@router.put("/{category_id}")
def set_budget(category_id: int, body: BudgetBody):
    with db_cursor() as conn:
        conn.execute(
            """INSERT INTO budgets(category_id, monthly_limit) VALUES(?, ?)
               ON CONFLICT(category_id) DO UPDATE SET monthly_limit=excluded.monthly_limit""",
            (category_id, body.monthly_limit),
        )
    return {"ok": True}


@router.delete("/{category_id}")
def delete_budget(category_id: int):
    with db_cursor() as conn:
        conn.execute("DELETE FROM budgets WHERE category_id=?", (category_id,))
    return {"ok": True}
