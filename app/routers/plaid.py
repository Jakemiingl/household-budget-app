"""Plaid endpoints: connect banks and sync accounts + transactions into SQLite."""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import budget_engine, plaid_client
from ..db import db_cursor

router = APIRouter()


class ConnectBody(BaseModel):
    member_id: int | None = None
    institution_id: str = "ins_109508"  # a Plaid Sandbox test bank


class PollBody(BaseModel):
    link_token: str


def _store_accounts(conn, item_id: str, member_id: int | None) -> None:
    for a in plaid_client.get_accounts(_access_token(conn, item_id)):
        conn.execute(
            """INSERT INTO accounts(id,item_id,member_id,name,official_name,type,
                   subtype,mask,current_balance,available_balance,currency,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
               ON CONFLICT(id) DO UPDATE SET
                   current_balance=excluded.current_balance,
                   available_balance=excluded.available_balance,
                   updated_at=datetime('now')""",
            (a["id"], item_id, member_id, a["name"], a["official_name"], a["type"],
             a["subtype"], a["mask"], a["current_balance"], a["available_balance"],
             a["currency"]),
        )


def _access_token(conn, item_id: str) -> str:
    row = conn.execute(
        "SELECT access_token FROM plaid_items WHERE item_id=?", (item_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Unknown Plaid item")
    return row["access_token"]


def _sync_item(conn, item_id: str) -> int:
    row = conn.execute(
        "SELECT access_token, cursor FROM plaid_items WHERE item_id=?", (item_id,)
    ).fetchone()
    result = plaid_client.sync_transactions(row["access_token"], row["cursor"])

    n = 0
    for t in result["added"] + result["modified"]:
        cid, rid = budget_engine.categorize_with_rule(
            conn, t["name"], t["merchant_name"], t["amount"]
        )
        conn.execute(
            """INSERT INTO transactions(id,account_id,date,name,merchant_name,amount,
                   currency,plaid_category,plaid_pfc,category_id,category_rule_id,pending)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   amount=excluded.amount, pending=excluded.pending,
                   merchant_name=excluded.merchant_name,
                   plaid_pfc=COALESCE(excluded.plaid_pfc, transactions.plaid_pfc)""",
            (t["id"], t["account_id"], t["date"], t["name"], t["merchant_name"],
             t["amount"], t["currency"], t["plaid_category"], t["plaid_pfc"],
             cid, rid, t["pending"]),
        )
        n += 1
    for tid in result["removed"]:
        conn.execute("DELETE FROM transactions WHERE id=?", (tid,))

    conn.execute(
        "UPDATE plaid_items SET cursor=? WHERE item_id=?", (result["cursor"], item_id)
    )
    return n


def _initial_sync(conn, item_id: str, attempts: int = 6, delay: float = 3.0) -> int:
    """Plaid prepares transactions asynchronously after an item is created, so
    the first sync often returns nothing. Retry a few times so a freshly
    connected bank shows its transactions on the first click.
    """
    total = 0
    for i in range(attempts):
        total += _sync_item(conn, item_id)
        if total > 0:
            break
        if i < attempts - 1:
            time.sleep(delay)
    return total


@router.post("/hosted-link")
def hosted_link():
    """Start a Plaid-Hosted Link session; returns a URL to open in the browser."""
    try:
        return plaid_client.create_hosted_link()
    except Exception as e:
        raise HTTPException(400, str(e))


@router.post("/hosted-link/poll")
def hosted_link_poll(body: PollBody):
    """Check a hosted-link session; once finished, exchange + sync any banks."""
    try:
        res = plaid_client.get_link_results(body.link_token)
    except Exception as e:
        raise HTTPException(400, str(e))

    if not res["items"]:
        return {"status": "finished" if res["finished"] else "pending"}

    connected, total = [], 0
    with db_cursor() as conn:
        for it in res["items"]:
            try:
                ex = plaid_client.exchange_public_token(it["public_token"])
            except Exception:
                continue  # already exchanged on a prior poll; skip
            conn.execute(
                """INSERT OR IGNORE INTO plaid_items(item_id,access_token,member_id)
                   VALUES(?,?,1)""",
                (ex["item_id"], ex["access_token"]),
            )
            _store_accounts(conn, ex["item_id"], 1)
            total += _initial_sync(conn, ex["item_id"])
            connected.append(it["institution"] or "your bank")
    return {"status": "connected", "institutions": connected,
            "transactions_added": total}


@router.post("/sandbox-connect")
def sandbox_connect(body: ConnectBody):
    """Sandbox-only convenience: connect a fake bank end-to-end, no browser."""
    try:
        public_token = plaid_client.sandbox_public_token(body.institution_id)
        ex = plaid_client.exchange_public_token(public_token)
    except Exception as e:
        raise HTTPException(400, str(e))
    with db_cursor() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO plaid_items(item_id,access_token,member_id)
               VALUES(?,?,?)""",
            (ex["item_id"], ex["access_token"], body.member_id),
        )
        _store_accounts(conn, ex["item_id"], body.member_id)
        added = _initial_sync(conn, ex["item_id"])
    return {"item_id": ex["item_id"], "transactions_added": added}


@router.post("/sync")
def sync_all():
    total = 0
    with db_cursor() as conn:
        items = conn.execute("SELECT item_id FROM plaid_items").fetchall()
        for it in items:
            total += _sync_item(conn, it["item_id"])
    return {"items": len(items), "transactions_added": total}
