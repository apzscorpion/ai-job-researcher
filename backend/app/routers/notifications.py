"""
Notifications router: unseen résumé suggestions, email drafts and apply steps
that need the user. Backed by ActivityLog rows whose event is in NOTIFY_EVENTS.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends
from sqlmodel import Session, col, select

from app.auth import get_current_user
from app.db import get_session
from app.models import NOTIFY_EVENTS, ActivityLog, User, json_decode

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


def _unseen(user_id: int):
    return select(ActivityLog).where(
        ActivityLog.user_id == user_id,
        col(ActivityLog.event).in_(NOTIFY_EVENTS),
        ActivityLog.seen == False,  # noqa: E712
    )


@router.get("")
def list_notifications(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    rows = session.exec(_unseen(current_user.id).order_by(col(ActivityLog.ts).desc()).limit(50)).all()
    return {
        "items": [
            {"id": r.id, "event": r.event, "ts": r.ts, **(json_decode(r.detail, {}) or {})}
            for r in rows
        ]
    }


@router.post("/seen")
def mark_all_seen(
    current_user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Dict[str, int]:
    rows = session.exec(_unseen(current_user.id)).all()
    for r in rows:
        r.seen = True
        session.add(r)
    session.commit()
    return {"marked": len(rows)}
