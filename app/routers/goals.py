"""Financial goals + timeline projections + purchase simulation."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import goal_engine
from ..db import db_cursor

router = APIRouter()


class GoalBody(BaseModel):
    name: str
    target_amount: float
    current_amount: float = 0.0
    target_date: str | None = None
    priority: int = 100
    account_ids: list[str] = []  # accounts to track progress against (optional)


def _bind_accounts(conn, goal_id: int, account_ids: list[str]) -> None:
    """Reconcile a goal's bound accounts. Newly-bound accounts snapshot their
    current balance as the goal's starting point; already-bound accounts keep
    their original snapshot so progress isn't reset on an edit."""
    wanted = [a for a in dict.fromkeys(account_ids) if a]  # dedupe, keep order
    existing = {r["account_id"] for r in conn.execute(
        "SELECT account_id FROM goal_accounts WHERE goal_id=?", (goal_id,))}
    for aid in wanted:
        if aid in existing:
            continue
        row = conn.execute(
            "SELECT current_balance FROM accounts WHERE id=?", (aid,)
        ).fetchone()
        if not row:
            raise HTTPException(404, f"Account {aid} not found")
        conn.execute(
            "INSERT INTO goal_accounts(goal_id, account_id, start_balance) VALUES(?,?,?)",
            (goal_id, aid, row["current_balance"] or 0.0),
        )
    stale = existing - set(wanted)
    for aid in stale:
        conn.execute(
            "DELETE FROM goal_accounts WHERE goal_id=? AND account_id=?",
            (goal_id, aid),
        )


@router.get("")
def list_goals():
    with db_cursor() as conn:
        return goal_engine.project(conn)


@router.post("")
def create_goal(body: GoalBody):
    with db_cursor() as conn:
        cur = conn.execute(
            """INSERT INTO goals(name,target_amount,current_amount,target_date,priority)
               VALUES(?,?,?,?,?)""",
            (body.name, body.target_amount, body.current_amount,
             body.target_date, body.priority),
        )
        _bind_accounts(conn, cur.lastrowid, body.account_ids)
        return {"id": cur.lastrowid}


@router.put("/{goal_id}")
def update_goal(goal_id: int, body: GoalBody):
    with db_cursor() as conn:
        cur = conn.execute(
            """UPDATE goals SET name=?, target_amount=?, current_amount=?,
                                target_date=?, priority=? WHERE id=?""",
            (body.name, body.target_amount, body.current_amount,
             body.target_date, body.priority, goal_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Goal not found")
        _bind_accounts(conn, goal_id, body.account_ids)
        # Re-arming: if the goal was previously completed but its target/accounts
        # changed so it's no longer met, clear the sticky completion on next read.
        conn.execute(
            "UPDATE goals SET completed_at=NULL WHERE id=? AND completed_at IS NOT NULL",
            (goal_id,),
        )
    return {"ok": True}


@router.delete("/{goal_id}")
def delete_goal(goal_id: int):
    with db_cursor() as conn:
        cur = conn.execute("DELETE FROM goals WHERE id=?", (goal_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Goal not found")
    return {"ok": True}
