"""Developer helpers: load sample data / reset, so the app is usable with no
Plaid setup. Safe to keep — it only touches your local database.
"""
from fastapi import APIRouter

from .. import sample_data
from ..db import db_cursor

router = APIRouter()


@router.post("/load-sample")
def load_sample(months: int = 4):
    with db_cursor() as conn:
        return sample_data.load(conn, months=months)


@router.post("/reset")
def reset():
    with db_cursor() as conn:
        for tbl in ("transactions", "accounts", "plaid_items", "goals"):
            conn.execute(f"DELETE FROM {tbl}")
    return {"ok": True}
