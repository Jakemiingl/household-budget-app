"""Thin wrapper around the Plaid SDK.

Plaid flow:
  1. create a Link token  -> front-end opens Plaid Link with it
  2. user logs into their bank -> Link returns a public_token
  3. exchange public_token -> long-lived access_token (stored locally)
  4. transactions_sync(access_token, cursor) -> incremental transactions

In Sandbox we can skip the browser UI with sandbox_public_token_create,
which makes end-to-end testing easy once sandbox keys are in .env.
"""
from __future__ import annotations

import plaid
from plaid.api import plaid_api
from plaid.model.accounts_get_request import AccountsGetRequest
from plaid.model.country_code import CountryCode
from plaid.model.item_public_token_exchange_request import (
    ItemPublicTokenExchangeRequest,
)
from plaid.model.link_token_create_hosted_link import LinkTokenCreateHostedLink
from plaid.model.link_token_create_request import LinkTokenCreateRequest
from plaid.model.link_token_create_request_user import LinkTokenCreateRequestUser
from plaid.model.link_token_get_request import LinkTokenGetRequest
from plaid.model.products import Products
from plaid.model.sandbox_public_token_create_request import (
    SandboxPublicTokenCreateRequest,
)
from plaid.model.transactions_sync_request import TransactionsSyncRequest

from .config import settings

_ENV_HOSTS = {
    "sandbox": plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}


def _client() -> plaid_api.PlaidApi:
    if not settings.plaid_configured:
        raise RuntimeError(
            "Plaid is not configured. Add PLAID_CLIENT_ID and PLAID_SECRET to .env."
        )
    config = plaid.Configuration(
        host=_ENV_HOSTS.get(settings.plaid_env, plaid.Environment.Sandbox),
        api_key={
            "clientId": settings.plaid_client_id,
            "secret": settings.plaid_secret,
        },
    )
    return plaid_api.PlaidApi(plaid.ApiClient(config))


def create_hosted_link() -> dict:
    """Create a Plaid-Hosted Link session. The user completes the entire bank
    login (including OAuth) on Plaid's own domain, so we don't need to register
    an HTTPS OAuth redirect for our localhost app. We poll get_link_results()
    for the resulting public_token. No completion_redirect_uri is set, so Plaid
    shows its own 'all done' screen and the user simply closes the tab.
    """
    req = LinkTokenCreateRequest(
        products=[Products("transactions")],
        client_name="Household Budget",
        country_codes=[CountryCode("US")],
        language="en",
        user=LinkTokenCreateRequestUser(client_user_id="household"),
        hosted_link=LinkTokenCreateHostedLink(),
    )
    res = _client().link_token_create(req)
    return {"link_token": res["link_token"], "hosted_link_url": res["hosted_link_url"]}


def get_link_results(link_token: str) -> dict:
    """Poll a (hosted) Link session: return whether it finished and any
    public_tokens produced. Safe to call repeatedly while the user is in Link.
    """
    res = _client().link_token_get(LinkTokenGetRequest(link_token=link_token)).to_dict()
    finished, items = False, []
    for session in res.get("link_sessions") or []:
        if session.get("finished_at"):
            finished = True
        results = session.get("results") or {}
        for iar in results.get("item_add_results") or []:
            pt = iar.get("public_token")
            if pt:
                inst = (iar.get("institution") or {}).get("name")
                items.append({"public_token": pt, "institution": inst})
    return {"finished": finished, "items": items}


def exchange_public_token(public_token: str) -> dict:
    res = _client().item_public_token_exchange(
        ItemPublicTokenExchangeRequest(public_token=public_token)
    )
    return {"access_token": res["access_token"], "item_id": res["item_id"]}


def sandbox_public_token(institution_id: str = "ins_109508") -> str:
    """Sandbox-only: mint a public_token for a fake bank, no browser needed."""
    res = _client().sandbox_public_token_create(
        SandboxPublicTokenCreateRequest(
            institution_id=institution_id,
            initial_products=[Products("transactions")],
        )
    )
    return res["public_token"]


def get_accounts(access_token: str) -> list[dict]:
    res = _client().accounts_get(AccountsGetRequest(access_token=access_token))
    out = []
    for a in res["accounts"]:
        bal = a["balances"]
        out.append(
            {
                "id": a["account_id"],
                "name": a.get("name"),
                "official_name": a.get("official_name"),
                "type": str(a["type"]),
                "subtype": str(a.get("subtype")) if a.get("subtype") else None,
                "mask": a.get("mask"),
                "current_balance": bal.get("current"),
                "available_balance": bal.get("available"),
                "currency": bal.get("iso_currency_code") or "USD",
            }
        )
    return out


def sync_transactions(access_token: str, cursor: str | None) -> dict:
    """Return added/modified/removed transactions plus the next cursor."""
    added, modified, removed = [], [], []
    has_more = True
    while has_more:
        req = TransactionsSyncRequest(access_token=access_token)
        if cursor:
            req.cursor = cursor
        res = _client().transactions_sync(req)
        added.extend(res["added"])
        modified.extend(res["modified"])
        removed.extend(res["removed"])
        cursor = res["next_cursor"]
        has_more = res["has_more"]

    def shape(t) -> dict:
        # Plaid's newer personal_finance_category (the legacy `category` list is
        # deprecated and increasingly NULL). Store the 'detailed' value.
        pfc = t.get("personal_finance_category")
        pfc_detailed = None
        if pfc is not None:
            try:
                pfc_detailed = pfc.get("detailed")
            except (AttributeError, TypeError):
                pfc_detailed = getattr(pfc, "detailed", None)
        return {
            "id": t["transaction_id"],
            "account_id": t["account_id"],
            "date": str(t["date"]),
            "name": t.get("name"),
            "merchant_name": t.get("merchant_name"),
            "amount": float(t["amount"]),
            "currency": t.get("iso_currency_code") or "USD",
            "plaid_category": ", ".join(t["category"]) if t.get("category") else None,
            "plaid_pfc": pfc_detailed,
            "pending": 1 if t.get("pending") else 0,
        }

    return {
        "added": [shape(t) for t in added],
        "modified": [shape(t) for t in modified],
        "removed": [t["transaction_id"] for t in removed],
        "cursor": cursor,
    }
