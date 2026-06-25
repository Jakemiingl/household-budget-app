"""FastAPI entry point. Serves the local web UI and the JSON API.

Runs only on 127.0.0.1 (your machine). Launch with start.bat or:
    .venv\\Scripts\\python -m app.main
"""
import asyncio
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import budget_engine, goal_engine, telegram_bot
from .config import WEB_DIR, settings
from .db import db_cursor, init_db
from .routers import (
    accounts, budgets, charts, chat, dev, goals, plaid, recurring, rules,
    transactions,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with db_cursor() as conn:        # capture net-worth + goal points each launch
        budget_engine.record_snapshot(conn)
        goal_engine.record_goal_snapshots(conn)
    bot_task = None
    if settings.telegram_bot_token:
        bot_task = asyncio.create_task(telegram_bot.run_bot())
    yield
    if bot_task:
        bot_task.cancel()
        try:
            await bot_task
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(title="Household Budget", lifespan=lifespan)

app.include_router(plaid.router, prefix="/api/plaid", tags=["plaid"])
app.include_router(accounts.router, prefix="/api/accounts", tags=["accounts"])
app.include_router(charts.router, prefix="/api/charts", tags=["charts"])
app.include_router(transactions.router, prefix="/api/transactions", tags=["transactions"])
app.include_router(budgets.router, prefix="/api/budgets", tags=["budgets"])
app.include_router(goals.router, prefix="/api/goals", tags=["goals"])
app.include_router(rules.router, prefix="/api/rules", tags=["rules"])
app.include_router(recurring.router, prefix="/api/recurring", tags=["recurring"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(dev.router, prefix="/api/dev", tags=["dev"])


@app.get("/api/status")
def status():
    return {
        "plaid_configured": settings.plaid_configured,
        "plaid_env": settings.plaid_env,
        "claude_model": settings.claude_model,
        "telegram_configured": bool(settings.telegram_bot_token),
        "telegram_allowed_count": len(settings.telegram_allowed_set),
    }


# Serve the single-page UI. Static assets under /static, index at root.
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


def main():
    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
