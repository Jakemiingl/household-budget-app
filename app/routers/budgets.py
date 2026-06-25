"""Budgets, monthly summary, and cash-flow trend."""
from fastapi import APIRouter
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
        return {
            "month": summary["month"],
            "budgets": budget_engine.budget_vs_actual(conn, month),
            # This month's surplus (income − expenses) — headline figure on the tab.
            "surplus": summary["net"],
            "income": summary["income"],
            "expenses": summary["expenses"],
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
