"""
Brain router: the apply-jobs Claude Code skill reads the site agent's step prompts and answers them.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_user
from app.models import User
from app.services import brain

router = APIRouter(prefix="/api/brain", tags=["brain"])


@router.get("/next")
async def next_prompt(wait: float = 50.0, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    got = await brain.next_prompt(current_user.id, max(0.0, min(wait, 55.0)))
    return {"id": got[0], "prompt": got[1]} if got else {"id": None, "prompt": None}


class Answer(BaseModel):
    text: str


@router.post("/answer/{prompt_id}")
def answer(prompt_id: str, body: Answer, current_user: User = Depends(get_current_user)) -> Dict[str, Any]:
    if not brain.answer(current_user.id, prompt_id, body.text):
        raise HTTPException(status_code=404, detail="That step already timed out or was answered")
    return {"ok": True}
