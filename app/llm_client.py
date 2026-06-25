"""AI layer. Shells out to the local Claude Code CLI in print mode, which uses
your Claude *subscription* (no API key, no per-token bill).

Design choices that keep this cheap, reliable, and safe:
- Default model is Haiku (light on your plan limits); configurable in .env.
- The prompt is piped via STDIN, so arbitrarily long financial context never
  hits shell-argument quoting limits.
- --json-schema forces a structured, parseable response.
- The model only *reasons over numbers we already computed in Python*. It never
  does the math itself, so answers can't drift from your real figures.

Swapping providers later (OpenAI / Anthropic API) means reimplementing only the
`complete()` function below.
"""
from __future__ import annotations

import json
import re
import subprocess

from .config import settings

# We ask the model to emit this shape as JSON in its reply, then parse it.
# (The CLI's --json-schema flag is unreliable across versions, so we don't use it.)
OUTPUT_INSTRUCTION = """Respond with ONLY a single JSON object, no prose or \
code fences, in exactly this shape:
{"answer": "<conversational answer, a few sentences>",
 "affordable": <true|false|null — true/false only for purchase questions>,
 "key_points": ["<short takeaway with figures>", "..."],
 "proposed_budgets": [{"category": "<EXACT expense category name from context>", "monthly_limit": <number>, "rationale": "<short why>"}],
 "proposed_goals": [{"name": "<goal name>", "target_amount": <number>, "current_amount": <number>, "target_date": "<YYYY-MM-DD or null>", "priority": <int, 1=fund first>, "rationale": "<short why>"}]}
Include proposed_budgets ONLY when the user asks for help building/adjusting a \
budget; include proposed_goals ONLY when they ask to set/plan a goal. Otherwise \
make both empty arrays []. Category names in proposed_budgets MUST exactly match \
names in context.expense_categories."""

SYSTEM = """You are the household's personal finance assistant inside a local \
budgeting app. You are speaking with a married couple about THEIR money.

You are given a CONTEXT block of real, already-computed figures from their \
accounts (net worth, monthly cash flow, average spend per category, budget \
status, goal projections, debts, and when relevant a purchase simulation). \
Trust these numbers exactly and never invent or recompute them. If the context \
lacks something needed, say so.

When they ask whether they can afford a purchase, base your verdict on the \
provided purchase simulation: whether it's payable from assets, how many months \
of surplus it equals, and how much it delays each financial goal. Be concrete \
and cite the actual dollar figures and dates.

When they ask for help BUILDING A BUDGET, propose a realistic monthly limit for \
each major expense category in proposed_budgets, grounded in their \
category_averages: trim discretionary categories modestly, keep essentials \
near actuals, and leave enough surplus to fund their goals and pay down debt. \
Briefly explain the plan in `answer`.

When they ask to SET A GOAL, fill proposed_goals with sensible target amount, \
date, and priority based on their cash flow and existing goals; note in `answer` \
roughly how long it will take given their surplus.

Be warm but honest; if a purchase or plan delays a goal, say so plainly. Keep \
`answer` to a few sentences."""

_TIMEOUT_S = 120


class LLMError(RuntimeError):
    pass


def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of the model's reply."""
    text = text.strip()
    # Strip ```json fences if present.
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _call(prompt: str) -> str:
    """Run the Claude CLI in print mode and return the model's text reply."""
    cmd = (
        f'"{settings.claude_cli_path}" -p '
        f'--model {settings.claude_model} '
        f'--output-format json'
    )
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True,
            shell=True, timeout=_TIMEOUT_S, encoding="utf-8",
        )
    except subprocess.TimeoutExpired as e:
        raise LLMError("The AI request timed out.") from e

    if proc.returncode != 0:
        raise LLMError(
            f"Claude CLI failed (exit {proc.returncode}): "
            f"{(proc.stderr or proc.stdout or '').strip()[:500]}"
        )
    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise LLMError(f"Could not parse Claude output: {proc.stdout[:300]}") from e
    if envelope.get("is_error"):
        raise LLMError(envelope.get("result") or "Claude returned an error.")
    return envelope.get("result", "")


def complete(question: str, context: dict) -> dict:
    """Send question + financial context to Claude; return structured dict."""
    prompt = (
        f"{SYSTEM}\n\n"
        f"=== CONTEXT (real figures, JSON) ===\n"
        f"{json.dumps(context, indent=2, default=str)}\n"
        f"=== END CONTEXT ===\n\n"
        f"User question: {question}\n\n"
        f"{OUTPUT_INSTRUCTION}"
    )
    result_text = _call(prompt)
    structured = _extract_json(result_text)
    if structured is None:
        # Model answered in prose; present it as-is rather than failing.
        structured = {"answer": result_text.strip()}

    structured.setdefault("answer", "")
    structured.setdefault("affordable", None)
    structured.setdefault("key_points", [])
    structured.setdefault("proposed_budgets", [])
    structured.setdefault("proposed_goals", [])
    return structured


_CATEGORIZE_SYSTEM = """You categorize bank transactions for a budgeting app. \
You are given a numbered list of uncategorized transactions (merchant name, \
description, amount) and the list of allowed category names. For each \
transaction, pick the single best category from the allowed list, and propose a \
short lowercase keyword `pattern` (usually the merchant, e.g. "starbucks", \
"gusto", "touchstone climbing") that would reliably match this and similar \
transactions in the future. Use the merchant/description text to infer the \
category. If unsure, use "Uncategorized" and your best-guess pattern."""


def suggest_categories(transactions: list[dict], categories: list[str]) -> list[dict]:
    """Ask Claude to suggest a category + keyword for each uncategorized txn.

    `transactions` items need keys: i (index), name, merchant, amount.
    Returns a list of {i, category, pattern}.
    """
    if not transactions:
        return []
    payload = {"allowed_categories": categories, "transactions": transactions}
    instruction = (
        'Respond with ONLY a JSON object: '
        '{"suggestions": [{"i": <index>, "category": "<allowed category>", '
        '"pattern": "<short lowercase keyword>"}]} — one entry per transaction.'
    )
    prompt = (
        f"{_CATEGORIZE_SYSTEM}\n\n"
        f"=== DATA (JSON) ===\n{json.dumps(payload, default=str)}\n=== END ===\n\n"
        f"{instruction}"
    )
    obj = _extract_json(_call(prompt)) or {}
    return obj.get("suggestions", [])


def available() -> bool:
    """Quick check that the Claude CLI is reachable."""
    try:
        proc = subprocess.run(
            f'"{settings.claude_cli_path}" --version',
            capture_output=True, text=True, shell=True, timeout=15,
        )
        return proc.returncode == 0
    except Exception:
        return False
