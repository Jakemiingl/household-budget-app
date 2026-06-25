"""Conversational endpoint: 'Can we afford X?' and general money questions.

The heavy lifting is in assistant.py (shared with the Telegram bot). The money
math is computed in Python; the LLM only reasons over those figures.
"""
from fastapi import APIRouter
from pydantic import BaseModel

from .. import assistant, llm_client
from ..db import db_cursor

router = APIRouter()


class ChatBody(BaseModel):
    question: str


@router.get("/health")
def health():
    return {"claude_available": llm_client.available()}


@router.post("")
def chat(body: ChatBody):
    try:
        return assistant.respond(body.question)
    except llm_client.LLMError as e:
        with db_cursor() as conn:
            context = assistant.build_context(conn, body.question)
        return {"answer": f"⚠️ {e}", "key_points": [], "affordable": None,
                "proposed_budgets": [], "proposed_goals": [],
                "context": context, "error": True}
