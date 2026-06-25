"""Transactions list, categories, and re-categorization."""
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import budget_engine
from ..db import db_cursor

router = APIRouter()


class CategoryUpdate(BaseModel):
    category_id: int


class CategoryBody(BaseModel):
    name: str
    kind: str = "expense"  # income | expense | transfer


def _months_ago(n: int) -> str:
    """ISO date n months before today (day clamped to 28 to avoid overflow)."""
    today = date.today()
    y, m = today.year, today.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(today.day, 28)).isoformat()


@router.get("")
def list_transactions(month: str | None = None, q: str | None = None,
                      limit: int = 100, offset: int = 0):
    """List transactions, paginated. With `q`, search the last 6 months by
    name/merchant (ignores `month`); otherwise list a single month (or the most
    recent). Returns `total` so the UI can page through with limit/offset."""
    select = """SELECT t.id, t.date, t.name, t.merchant_name, t.amount, t.pending,
                    c.name AS category, c.id AS category_id, a.name AS account,
                    t.category_rule_id, r.pattern AS rule_pattern
             FROM transactions t
             LEFT JOIN categories c ON t.category_id = c.id
             LEFT JOIN accounts a ON t.account_id = a.id
             LEFT JOIN category_rules r ON t.category_rule_id = r.id"""
    where: list = []
    params: list = []
    q = (q or "").strip()
    if q:
        like = f"%{q.lower()}%"
        where.append(
            "(lower(coalesce(t.name,'')) LIKE ? OR lower(coalesce(t.merchant_name,'')) LIKE ?)"
        )
        params += [like, like]
        where.append("t.date >= ?")
        params.append(_months_ago(6))
    elif month:
        where.append("substr(t.date,1,7) = ?")
        params.append(month)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    with db_cursor() as conn:
        # The WHERE filters touch only `t` columns, so the count needs no joins.
        total = conn.execute(
            "SELECT COUNT(*) FROM transactions t" + where_sql, params
        ).fetchone()[0]
        rows = conn.execute(
            select + where_sql + " ORDER BY t.date DESC, t.id LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return {
            "transactions": [dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }


@router.get("/uncategorized")
def list_uncategorized(limit: int = 200):
    """Transactions that landed in 'Uncategorized' — the queue to tag/rule.

    Each row carries Plaid's PFC hint (`pfc_category` / `pfc_category_id`) so the
    UI can pre-fill a suggested category even when the description is masked.
    """
    with db_cursor() as conn:
        rows = conn.execute(
            """SELECT t.id, t.date, t.name, t.merchant_name, t.amount, t.plaid_pfc,
                      a.name AS account
               FROM transactions t
               JOIN categories c ON t.category_id = c.id
               LEFT JOIN accounts a ON t.account_id = a.id
               WHERE c.name = 'Uncategorized'
               ORDER BY t.date DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        cat_id = {r["name"]: r["id"]
                  for r in conn.execute("SELECT id, name FROM categories")}
    out = []
    for r in rows:
        d = dict(r)
        pfc_cat = budget_engine.pfc_to_category_name(r["plaid_pfc"])
        d["pfc_category"] = pfc_cat
        d["pfc_category_id"] = cat_id.get(pfc_cat) if pfc_cat else None
        out.append(d)
    return {"transactions": out}


@router.get("/mismatches")
def list_mismatches(limit: int = 200):
    """Transactions a keyword rule categorized into a category whose KIND
    disagrees with Plaid's PFC hint (e.g. a rule tagged it an expense but Plaid
    says it's a transfer/income). Kind-level only — within-kind differences
    (Groceries vs Shopping) are personal preference, not errors — so the list
    stays high-signal."""
    with db_cursor() as conn:
        cats = {r["name"]: (r["id"], r["kind"])
                for r in conn.execute("SELECT id, name, kind FROM categories")}
        rows = conn.execute(
            """SELECT t.id, t.date, t.name, t.merchant_name, t.amount, t.plaid_pfc,
                      c.name AS category, c.id AS category_id, c.kind AS category_kind,
                      t.category_rule_id AS rule_id, r.pattern AS rule_pattern,
                      a.name AS account
               FROM transactions t
               JOIN categories c ON t.category_id = c.id
               LEFT JOIN category_rules r ON t.category_rule_id = r.id
               LEFT JOIN accounts a ON t.account_id = a.id
               WHERE t.category_rule_id IS NOT NULL AND t.plaid_pfc IS NOT NULL
                     AND t.pfc_ignored = 0 AND COALESCE(r.pfc_mute, 0) = 0
               ORDER BY t.date DESC"""
        ).fetchall()
    out = []
    for r in rows:
        pfc_cat = budget_engine.pfc_to_category_name(r["plaid_pfc"])
        pfc = cats.get(pfc_cat) if pfc_cat else None
        if not pfc or pfc[1] == r["category_kind"]:
            continue  # no mapping, or same kind → not a meaningful mismatch
        d = dict(r)
        d["pfc_category"] = pfc_cat
        d["pfc_category_id"] = pfc[0]
        out.append(d)
        if len(out) >= limit:
            break
    return {"mismatches": out}


@router.get("/categories")
def list_categories():
    with db_cursor() as conn:
        rows = conn.execute(
            "SELECT id, name, kind FROM categories ORDER BY kind, name"
        ).fetchall()
        return {"categories": [dict(r) for r in rows]}


@router.post("/categories")
def create_category(body: CategoryBody):
    """Add a new category (income | expense | transfer)."""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name cannot be empty")
    if body.kind not in ("income", "expense", "transfer"):
        raise HTTPException(400, "kind must be income, expense, or transfer")
    with db_cursor() as conn:
        if conn.execute(
            "SELECT 1 FROM categories WHERE lower(name)=lower(?)", (name,)
        ).fetchone():
            raise HTTPException(409, "A category with that name already exists")
        cur = conn.execute(
            "INSERT INTO categories(name, kind) VALUES (?, ?)", (name, body.kind)
        )
        return {"id": cur.lastrowid, "name": name, "kind": body.kind}


@router.post("/recategorize")
def recategorize():
    with db_cursor() as conn:
        n = budget_engine.recategorize_all(conn)
    return {"recategorized": n}


@router.post("/{txn_id}/ignore-mismatch")
def ignore_mismatch(txn_id: str):
    """Dismiss a single transaction's PFC mismatch without changing its category."""
    with db_cursor() as conn:
        cur = conn.execute(
            "UPDATE transactions SET pfc_ignored=1 WHERE id=?", (txn_id,)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Transaction not found")
    return {"ok": True}


@router.patch("/{txn_id}/category")
def set_category(txn_id: str, body: CategoryUpdate):
    with db_cursor() as conn:
        # Manual override: clear the rule link so it shows as user-set, not by a rule.
        cur = conn.execute(
            "UPDATE transactions SET category_id=?, category_rule_id=NULL WHERE id=?",
            (body.category_id, txn_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Transaction not found")
        # Now categorized — drop any saved suggestion for it.
        conn.execute(
            "DELETE FROM category_suggestions WHERE transaction_id=?", (txn_id,)
        )
    return {"ok": True}
