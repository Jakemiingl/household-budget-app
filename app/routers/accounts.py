"""Accounts & net worth — including manually-added (non-Plaid) accounts.

Manual accounts have item_id IS NULL. Plaid sync never touches them, so their
balances are whatever you last entered. Update them on a regular basis to keep
net worth accurate.
"""
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import budget_engine
from ..db import db_cursor

router = APIRouter()


class ManualAccount(BaseModel):
    name: str
    type: str = "depository"        # depository | credit | loan | investment | asset
    subtype: str | None = None      # checking | savings | vehicle | rv | ...
    current_balance: float = 0.0
    currency: str = "USD"
    linked_account_id: str | None = None  # asset → the loan that financed it
    interest_rate: float | None = None    # APR % (credit/loan only)
    monthly_payment: float | None = None  # $/mo toward the debt (credit/loan only)


class AccountUpdate(BaseModel):
    name: str | None = None
    current_balance: float | None = None
    # Present (even as "") = change the loan link ("" clears it); omitted = leave as-is.
    linked_account_id: str | None = None


class TermsUpdate(BaseModel):
    interest_rate: float | None = None    # APR %; null clears it
    monthly_payment: float | None = None  # $/mo; null clears it


def _require_manual(conn, account_id: str):
    row = conn.execute(
        "SELECT item_id FROM accounts WHERE id=?", (account_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Account not found")
    if row["item_id"] is not None:
        raise HTTPException(
            400, "This is a Plaid-synced account; its balance updates on sync."
        )


def _validate_link(conn, asset_id: str, linked_id: str | None) -> str | None:
    """An asset may be linked only to an existing loan/credit account (its debt)."""
    if not linked_id:
        return None
    if linked_id == asset_id:
        raise HTTPException(400, "An asset can't be linked to itself.")
    row = conn.execute(
        "SELECT type FROM accounts WHERE id=?", (linked_id,)
    ).fetchone()
    if not row:
        raise HTTPException(400, "Linked loan account not found.")
    if row["type"] not in ("credit", "loan"):
        raise HTTPException(
            400, "An asset can only be linked to a loan or credit-card account."
        )
    return linked_id


@router.get("/debt-plan")
def debt_plan():
    with db_cursor() as conn:
        return budget_engine.debt_payoff_plan(conn)


@router.get("")
def list_accounts():
    with db_cursor() as conn:
        return {
            "accounts": budget_engine.accounts_summary(conn),
            "net_worth": budget_engine.net_worth(conn),
        }


@router.post("/manual")
def create_manual(body: ManualAccount):
    account_id = "man_" + uuid.uuid4().hex[:16]
    with db_cursor() as conn:
        linked = _validate_link(conn, account_id, body.linked_account_id)
        is_debt = body.type in ("credit", "loan")
        rate = body.interest_rate if is_debt else None
        payment = body.monthly_payment if is_debt else None
        conn.execute(
            """INSERT INTO accounts(id, item_id, member_id, name, type, subtype,
                   current_balance, available_balance, currency,
                   linked_account_id, interest_rate, monthly_payment, updated_at)
               VALUES(?, NULL, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (account_id, body.name, body.type, body.subtype,
             body.current_balance, body.current_balance, body.currency,
             linked, rate, payment),
        )
    return {"id": account_id}


@router.patch("/{account_id}")
def update_account(account_id: str, body: AccountUpdate):
    with db_cursor() as conn:
        _require_manual(conn, account_id)
        if body.name is not None:
            conn.execute(
                "UPDATE accounts SET name=? WHERE id=?", (body.name, account_id)
            )
        if body.current_balance is not None:
            conn.execute(
                """UPDATE accounts SET current_balance=?, available_balance=?,
                       updated_at=datetime('now') WHERE id=?""",
                (body.current_balance, body.current_balance, account_id),
            )
        if "linked_account_id" in body.model_fields_set:
            linked = _validate_link(conn, account_id, body.linked_account_id)
            conn.execute(
                "UPDATE accounts SET linked_account_id=? WHERE id=?",
                (linked, account_id),
            )
    return {"ok": True}


@router.patch("/{account_id}/terms")
def set_terms(account_id: str, body: TermsUpdate):
    """Set/clear a credit or loan account's APR and/or monthly payment.

    Only the fields included in the request are changed (send a field as null to
    clear it). Allowed on Plaid-synced accounts too (unlike balance): these are
    things you know, and Plaid's sync only updates the balance, so it won't clobber
    them.
    """
    with db_cursor() as conn:
        row = conn.execute(
            "SELECT type FROM accounts WHERE id=?", (account_id,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Account not found")
        if row["type"] not in ("credit", "loan"):
            raise HTTPException(
                400, "Interest rate and payment apply only to credit or loan accounts."
            )
        if body.interest_rate is not None and body.interest_rate < 0:
            raise HTTPException(400, "Interest rate can't be negative.")
        if body.monthly_payment is not None and body.monthly_payment < 0:
            raise HTTPException(400, "Monthly payment can't be negative.")
        if "interest_rate" in body.model_fields_set:
            conn.execute("UPDATE accounts SET interest_rate=? WHERE id=?",
                         (body.interest_rate, account_id))
        if "monthly_payment" in body.model_fields_set:
            conn.execute("UPDATE accounts SET monthly_payment=? WHERE id=?",
                         (body.monthly_payment, account_id))
    return {"ok": True}


@router.delete("/{account_id}")
def delete_account(account_id: str):
    with db_cursor() as conn:
        _require_manual(conn, account_id)
        conn.execute("DELETE FROM transactions WHERE account_id=?", (account_id,))
        conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
    return {"ok": True}
