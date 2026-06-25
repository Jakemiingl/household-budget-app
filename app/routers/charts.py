"""Chart images (PNG) for the web UI — the same renderer the Telegram reports use."""
from fastapi import APIRouter, Response

from .. import budget_engine, charts
from ..db import db_cursor

router = APIRouter()


def _png(data: bytes) -> Response:
    # no-store so the browser always fetches a freshly rendered chart
    return Response(content=data, media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.get("/goals.png")
def goals_png():
    with db_cursor() as conn:
        return _png(charts.goals_chart(conn))


@router.get("/networth.png")
def networth_png():
    with db_cursor() as conn:
        budget_engine.record_snapshot(conn)  # capture today's point whenever viewed
        return _png(charts.networth_chart(conn))


@router.get("/cashflow.png")
def cashflow_png(months: int = 6, include_current: bool = True):
    with db_cursor() as conn:
        return _png(charts.cashflow_chart(conn, months=months,
                                          include_current=include_current))
