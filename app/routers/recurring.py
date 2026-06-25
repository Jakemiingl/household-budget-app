"""Recurring charges / subscriptions endpoints."""
from datetime import date

from fastapi import APIRouter
from pydantic import BaseModel

from .. import recurring
from ..db import db_cursor

router = APIRouter()


class DismissBody(BaseModel):
    key: str
    label: str | None = None


@router.get("")
def list_recurring():
    with db_cursor() as conn:
        return recurring.detect(conn)


@router.post("/dismiss")
def dismiss(body: DismissBody):
    """Hide a recurring series (e.g. cancelled). It resurfaces if charged again
    after today."""
    with db_cursor() as conn:
        conn.execute(
            """INSERT INTO recurring_dismissed(merchant_key, label, dismissed_at)
               VALUES(?,?,?)
               ON CONFLICT(merchant_key) DO UPDATE SET
                   label=excluded.label, dismissed_at=excluded.dismissed_at""",
            (body.key, body.label, date.today().isoformat()),
        )
    return {"ok": True}


@router.post("/restore")
def restore(body: DismissBody):
    """Un-dismiss a previously removed recurring series."""
    with db_cursor() as conn:
        conn.execute(
            "DELETE FROM recurring_dismissed WHERE merchant_key=?", (body.key,)
        )
    return {"ok": True}


@router.get("/dismissed")
def list_dismissed():
    with db_cursor() as conn:
        rows = conn.execute(
            """SELECT merchant_key AS key, label, dismissed_at
               FROM recurring_dismissed ORDER BY dismissed_at DESC"""
        ).fetchall()
        return {"dismissed": [dict(r) for r in rows]}
