"""Categorization rules. Lets the category set build itself as you work:
tag an uncategorized transaction and (optionally) turn it into a keyword rule
that re-categorizes every matching transaction, past and future.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import budget_engine, llm_client
from ..db import db_cursor

router = APIRouter()


VALID_DIRECTIONS = ("any", "in", "out")


class RuleBody(BaseModel):
    pattern: str
    category_id: int
    priority: int = 50  # user-made rules beat the seeded defaults (priority 100)
    direction: str = "any"  # any | in (money in/amount<0) | out (money out/amount>0)


class SuggestionBody(BaseModel):
    category_id: int | None = None
    pattern: str | None = None


def _save_suggestion(conn, txn_id: str, category_id: int | None, pattern: str | None):
    conn.execute(
        """INSERT INTO category_suggestions(transaction_id, category_id, pattern)
           VALUES (?,?,?)
           ON CONFLICT(transaction_id) DO UPDATE SET
               category_id = excluded.category_id,
               pattern     = excluded.pattern,
               created_at  = datetime('now')""",
        (txn_id, category_id, (pattern or "").strip() or None),
    )


def _apply_pattern(conn, pattern: str, category_id: int, rule_id: int,
                   direction: str = "any") -> list[str]:
    """Set category (and link the rule) on every transaction whose name/merchant
    matches pattern AND whose sign matches the rule's direction; return the ids
    that were updated.

    Uses the same punctuation/space-insensitive match as sync-time categorize()
    so a rule like "google workspace" also catches the concatenated
    "GOOGLEWORKSPACE". Matching is done in Python (not SQL LIKE) so both paths
    normalize identically.
    """
    rows = conn.execute(
        "SELECT id, name, merchant_name, amount FROM transactions"
    ).fetchall()
    ids = [
        row["id"] for row in rows
        if budget_engine.rule_matches(pattern, row["name"], row["merchant_name"])
        and budget_engine.direction_matches(direction, row["amount"])
    ]
    for tid in ids:
        conn.execute(
            "UPDATE transactions SET category_id = ?, category_rule_id = ? WHERE id = ?",
            (category_id, rule_id, tid),
        )
    return ids


def _recategorize_subset(conn, txn_ids) -> None:
    """Re-evaluate a specific set of transactions against ALL current rules
    (used after a rule edit, so unrelated manual categorizations are untouched)."""
    for tid in set(txn_ids):
        row = conn.execute(
            "SELECT name, merchant_name, amount FROM transactions WHERE id = ?", (tid,)
        ).fetchone()
        if not row:
            continue
        cid, rid = budget_engine.categorize_with_rule(
            conn, row["name"], row["merchant_name"], row["amount"]
        )
        conn.execute(
            "UPDATE transactions SET category_id = ?, category_rule_id = ? WHERE id = ?",
            (cid, rid, tid),
        )


@router.get("")
def list_rules():
    with db_cursor() as conn:
        rows = conn.execute(
            """SELECT r.id, r.pattern, r.priority, r.direction,
                      r.category_id, c.name AS category
               FROM category_rules r JOIN categories c ON r.category_id = c.id
               ORDER BY r.priority ASC, r.pattern"""
        ).fetchall()
        return {"rules": [dict(r) for r in rows]}


@router.post("")
def create_rule(body: RuleBody):
    pattern = body.pattern.strip()
    if not pattern:
        raise HTTPException(400, "Pattern cannot be empty")
    if body.direction not in VALID_DIRECTIONS:
        raise HTTPException(400, f"direction must be one of {VALID_DIRECTIONS}")
    with db_cursor() as conn:
        if not conn.execute(
            "SELECT 1 FROM categories WHERE id=?", (body.category_id,)
        ).fetchone():
            raise HTTPException(404, "Category not found")
        cur = conn.execute(
            "INSERT INTO category_rules(pattern, category_id, priority, direction) "
            "VALUES (?,?,?,?)",
            (pattern, body.category_id, body.priority, body.direction),
        )
        affected_ids = _apply_pattern(
            conn, pattern, body.category_id, cur.lastrowid, body.direction
        )
        # These transactions are now categorized — drop their saved suggestions.
        for tid in affected_ids:
            conn.execute(
                "DELETE FROM category_suggestions WHERE transaction_id=?", (tid,)
            )
        return {"id": cur.lastrowid, "affected": len(affected_ids),
                "affected_ids": affected_ids}


@router.post("/suggest")
def suggest(limit: int = 40):
    """Use the assistant to suggest a category + keyword for each uncategorized
    transaction (looks at merchant/description). The UI pre-fills these so you
    just confirm. Nothing is changed until you create the rule.
    """
    with db_cursor() as conn:
        rows = conn.execute(
            """SELECT t.id, t.name, t.merchant_name, t.amount
               FROM transactions t JOIN categories c ON t.category_id = c.id
               WHERE c.name = 'Uncategorized' ORDER BY t.date DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        cat_rows = conn.execute(
            "SELECT id, name FROM categories ORDER BY name"
        ).fetchall()

    if not rows:
        return {"suggestions": []}

    cat_id_by_name = {r["name"].lower(): r["id"] for r in cat_rows}
    cat_names = [r["name"] for r in cat_rows]

    # Pass an index (i) instead of long Plaid ids for reliable round-tripping.
    by_index = {i: r["id"] for i, r in enumerate(rows)}
    payload = [
        {"i": i, "merchant": r["merchant_name"], "name": r["name"],
         "amount": r["amount"]}
        for i, r in enumerate(rows)
    ]
    try:
        raw = llm_client.suggest_categories(payload, cat_names)
    except llm_client.LLMError as e:
        raise HTTPException(502, f"AI suggestion failed: {e}")

    out = []
    for s in raw:
        txn_id = by_index.get(s.get("i"))
        if txn_id is None:
            continue
        cat_name = str(s.get("category", "")).strip()
        out.append({
            "transaction_id": txn_id,
            "category": cat_name,
            "category_id": cat_id_by_name.get(cat_name.lower()),
            "pattern": str(s.get("pattern", "")).strip(),
        })

    # Persist so suggestions survive closing/refreshing the window.
    with db_cursor() as conn:
        for s in out:
            _save_suggestion(conn, s["transaction_id"], s["category_id"], s["pattern"])
    return {"suggestions": out}


@router.get("/suggestions")
def list_suggestions():
    """Saved suggestions for still-uncategorized transactions, keyed by txn id."""
    with db_cursor() as conn:
        rows = conn.execute(
            """SELECT s.transaction_id, s.category_id, s.pattern
               FROM category_suggestions s
               JOIN transactions t ON t.id = s.transaction_id
               JOIN categories c ON t.category_id = c.id
               WHERE c.name = 'Uncategorized'"""
        ).fetchall()
    return {"suggestions": {
        r["transaction_id"]: {"category_id": r["category_id"], "pattern": r["pattern"]}
        for r in rows
    }}


@router.put("/suggestions/{txn_id}")
def save_suggestion(txn_id: str, body: SuggestionBody):
    """Upsert a single suggestion (e.g. after the user tweaks a row) so manual
    edits also survive a reload."""
    with db_cursor() as conn:
        _save_suggestion(conn, txn_id, body.category_id, body.pattern)
    return {"ok": True}


@router.put("/{rule_id}")
def update_rule(rule_id: int, body: RuleBody):
    """Edit a rule's keyword and/or category, then re-categorize only the
    transactions affected by the change (those this rule had set, plus any the
    new keyword now matches) — leaving unrelated manual categories untouched.
    """
    pattern = body.pattern.strip()
    if not pattern:
        raise HTTPException(400, "Pattern cannot be empty")
    if body.direction not in VALID_DIRECTIONS:
        raise HTTPException(400, f"direction must be one of {VALID_DIRECTIONS}")
    with db_cursor() as conn:
        if not conn.execute(
            "SELECT 1 FROM category_rules WHERE id=?", (rule_id,)
        ).fetchone():
            raise HTTPException(404, "Rule not found")
        if not conn.execute(
            "SELECT 1 FROM categories WHERE id=?", (body.category_id,)
        ).fetchone():
            raise HTTPException(404, "Category not found")

        old_linked = [
            r["id"] for r in conn.execute(
                "SELECT id FROM transactions WHERE category_rule_id=?", (rule_id,)
            )
        ]
        conn.execute(
            "UPDATE category_rules SET pattern=?, category_id=?, direction=? WHERE id=?",
            (pattern, body.category_id, body.direction, rule_id),
        )
        new_matches = [
            r["id"] for r in conn.execute(
                "SELECT id, name, merchant_name, amount FROM transactions"
            )
            if budget_engine.rule_matches(pattern, r["name"], r["merchant_name"])
            and budget_engine.direction_matches(body.direction, r["amount"])
        ]
        _recategorize_subset(conn, old_linked + new_matches)
    return {"ok": True, "affected": len(set(old_linked + new_matches))}


class MuteBody(BaseModel):
    muted: bool = True


@router.post("/{rule_id}/mute-pfc")
def mute_pfc(rule_id: int, body: MuteBody):
    """Suppress (or restore) Plaid-PFC mismatch flags for a rule — used when the
    rule's category intentionally differs from Plaid's (e.g. PayPal purchases we
    treat as spending though Plaid calls them transfers)."""
    with db_cursor() as conn:
        cur = conn.execute(
            "UPDATE category_rules SET pfc_mute=? WHERE id=?",
            (1 if body.muted else 0, rule_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Rule not found")
    return {"ok": True, "muted": body.muted}


@router.delete("/{rule_id}")
def delete_rule(rule_id: int):
    with db_cursor() as conn:
        if not conn.execute(
            "SELECT 1 FROM category_rules WHERE id=?", (rule_id,)
        ).fetchone():
            raise HTTPException(404, "Rule not found")
        # Remember which transactions this rule had set so we can re-evaluate them
        # against the remaining rules once it's gone.
        linked = [
            r["id"] for r in conn.execute(
                "SELECT id FROM transactions WHERE category_rule_id=?", (rule_id,)
            )
        ]
        conn.execute("DELETE FROM category_rules WHERE id=?", (rule_id,))
        # Re-categorize the orphaned transactions: a remaining rule may now apply,
        # otherwise they fall back to Income/Uncategorized.
        _recategorize_subset(conn, linked)
    return {"ok": True, "affected": len(linked)}
