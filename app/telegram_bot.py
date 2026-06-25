"""Telegram bot — message the budget assistant from your phone.

Uses long-polling (getUpdates), so the app only makes OUTBOUND requests to
Telegram. Your PC never needs to accept inbound connections (no port-forward,
no tunnel). Access is restricted to allowlisted chat IDs; unknown senders are
told their chat ID so you can add them, but get no financial data.
"""
from __future__ import annotations

import asyncio

import httpx

from . import assistant, llm_client
from .config import settings

_API = "https://api.telegram.org/bot{token}/{method}"
_POLL_TIMEOUT = 50  # seconds for long-poll


def format_reply(result: dict) -> str:
    lines: list[str] = []
    if result.get("affordable") is True:
        lines.append("✅ Yes, you can afford it")
    elif result.get("affordable") is False:
        lines.append("❌ Better to hold off")
    if result.get("answer"):
        lines.append(result["answer"])
    for p in result.get("key_points") or []:
        lines.append(f"• {p}")
    pb = result.get("proposed_budgets") or []
    if pb:
        lines.append("\n📊 Suggested budget (apply in the app):")
        lines += [f"• {b.get('category')}: ${b.get('monthly_limit')}" for b in pb]
    pg = result.get("proposed_goals") or []
    if pg:
        lines.append("\n🎯 Suggested goal (add in the app):")
        lines += [f"• {g.get('name')}: ${g.get('target_amount')} by {g.get('target_date') or 'n/a'}" for g in pg]
    return "\n".join(filter(None, lines)) or "(no answer)"


async def _send(client: httpx.AsyncClient, token: str, chat_id: int, text: str):
    try:
        await client.post(
            _API.format(token=token, method="sendMessage"),
            json={"chat_id": chat_id, "text": text},
        )
    except Exception:
        pass  # don't let a send failure kill the poll loop


def send_photo_sync(token: str, chat_ids, png: bytes, caption: str = "") -> int:
    """Send a PNG to each chat ID (blocking). Used by the scheduled report jobs,
    which run standalone (no event loop). Returns the number sent successfully.
    """
    sent = 0
    with httpx.Client(timeout=30) as client:
        for cid in chat_ids:
            try:
                r = client.post(
                    _API.format(token=token, method="sendPhoto"),
                    data={"chat_id": str(cid), "caption": caption},
                    files={"photo": ("chart.png", png, "image/png")},
                )
                if r.json().get("ok"):
                    sent += 1
            except Exception as e:
                print(f"[telegram] sendPhoto to {cid} failed: {e!r}")
    return sent


async def _handle(client, token, allowed, msg):
    chat_id = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    if not text:
        return

    allowed_flag = chat_id in allowed
    print(f"[telegram] msg chat_id={chat_id} allowed={allowed_flag}: {text[:60]!r}")

    if not allowed_flag:
        sender = msg.get("from", {})
        uname = sender.get("username") or sender.get("first_name") or "?"
        print(f"[telegram] message from unlisted chat_id={chat_id} (@{uname})")
        if allowed:
            await _send(client, token, chat_id,
                        "You're not authorized to use this budget assistant.\n"
                        f"Your chat ID is: {chat_id}")
        else:
            await _send(client, token, chat_id,
                        "Pairing mode. Your chat ID is:\n"
                        f"{chat_id}\n\nAdd it to TELEGRAM_ALLOWED_CHAT_IDS in .env "
                        "(comma-separated for multiple people) and restart the app.")
        return

    if text.startswith("/start") or text.startswith("/help"):
        await _send(client, token, chat_id,
                    "Hi! Ask me about your household budget — e.g.\n"
                    "• \"Can we afford a $2,000 couch?\"\n"
                    "• \"How are we doing on dining this month?\"\n"
                    "• \"Help me build a budget.\"")
        return

    try:
        result = await asyncio.to_thread(assistant.respond, text)
        reply = format_reply(result)
    except llm_client.LLMError as e:
        reply = f"⚠️ {e}"
    except Exception as e:  # never crash the loop on one bad message
        reply = f"⚠️ Something went wrong: {e}"
    await _send(client, token, chat_id, reply)


async def run_bot():
    """Poll Telegram for messages and answer them. Runs until cancelled."""
    token = settings.telegram_bot_token
    allowed = settings.telegram_allowed_set
    offset: int | None = None

    async with httpx.AsyncClient(timeout=_POLL_TIMEOUT + 15) as client:
        # Verify the token before polling. A transient network blip here must
        # NOT permanently disable the bot — retry with backoff (the poll loop
        # below already tolerates transient errors the same way). Only a genuine
        # bad-token response (ok=false) is fatal.
        attempt = 0
        while True:
            try:
                me = (await client.get(_API.format(token=token, method="getMe"))).json()
                if not me.get("ok"):
                    print(f"[telegram] invalid bot token: {me}")
                    return
                print(f"[telegram] bot @{me['result'].get('username')} online; "
                      f"{len(allowed) or 'no'} allowed chat id(s)")
                break
            except asyncio.CancelledError:
                raise
            except Exception as e:
                attempt += 1
                delay = min(60, 3 * attempt)  # back off, cap at 60s
                print(f"[telegram] could not reach Telegram (attempt {attempt}): "
                      f"{e!r}; retrying in {delay}s")
                await asyncio.sleep(delay)

        while True:
            try:
                params = {"timeout": _POLL_TIMEOUT}
                if offset is not None:
                    params["offset"] = offset
                resp = await client.get(
                    _API.format(token=token, method="getUpdates"), params=params
                )
                data = resp.json()
                if not data.get("ok"):
                    if data.get("error_code") == 409:
                        print("[telegram] 409 conflict — another instance is "
                              "polling this bot. Only run one copy of the app.")
                    await asyncio.sleep(3)
                    continue
                for upd in data.get("result", []):
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or upd.get("edited_message")
                    if msg:
                        await _handle(client, token, allowed, msg)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(3)  # transient network error; back off
