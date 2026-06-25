"""Shared assistant pipeline: build financial context + ask the LLM.

Used by both the in-app web chat (routers/chat.py) and the Telegram bot, so they
give identical answers.
"""
from __future__ import annotations

import re

from . import budget_engine, goal_engine, llm_client, recurring
from .db import db_cursor

# Matches "$4,000", "4000", "1.2k", "$3k". The optional k/K multiplier must be
# attached to the number (not the start of a word like "kitchen").
_AMOUNT_RE = re.compile(r"\$?\s*(\d[\d,]*(?:\.\d+)?)([kK])?(?![\w])")


def detect_amount(text: str) -> float | None:
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    num = float(m.group(1).replace(",", ""))
    if m.group(2):
        num *= 1000
    return num if num >= 20 else None  # ignore tiny incidental numbers


def build_context(conn, question: str) -> dict:
    expense_categories = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM categories WHERE kind='expense' ORDER BY name"
        ).fetchall()
    ]
    debts = [
        dict(r)
        for r in conn.execute(
            """SELECT name, type, subtype, current_balance, interest_rate,
                      monthly_payment FROM accounts
               WHERE type IN ('credit','loan') ORDER BY current_balance DESC"""
        ).fetchall()
    ]
    ctx = {
        "net_worth": budget_engine.net_worth(conn),
        "monthly_summary": budget_engine.monthly_summary(conn),
        "category_averages": budget_engine.category_averages(conn, months=3),
        "budget_vs_actual": budget_engine.budget_vs_actual(conn),
        "cash_flow_recent": budget_engine.cash_flow(conn, months=6),
        "average_monthly_surplus": budget_engine.average_monthly_surplus(conn),
        "goals": goal_engine.project(conn)["goals"],
        "debts": debts,
        "debt_payoff_plan": budget_engine.debt_payoff_plan(conn),
        "expense_categories": expense_categories,
    }
    rec = recurring.detect(conn)
    ctx["recurring"] = {
        "monthly_total": rec["monthly_total"],
        "active_count": rec["active_count"],
        "items": [i for i in rec["recurring"] if i["active"]][:15],
    }
    amount = detect_amount(question)
    if amount is not None:
        ctx["purchase_simulation"] = goal_engine.simulate_purchase(conn, amount)
    return ctx


def respond(question: str) -> dict:
    """Build context, ask the LLM, return the structured result (+ context).

    Raises llm_client.LLMError on AI failure.
    """
    with db_cursor() as conn:
        ctx = build_context(conn, question)
    result = llm_client.complete(question, ctx)
    result["context"] = ctx
    return result
