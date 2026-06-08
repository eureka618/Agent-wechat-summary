from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import UserEvent
from app.services.json_utils import dumps


ALLOWED_EVENT_TYPES = {
    "view_opportunity",
    "save_opportunity",
    "verify_opportunity",
    "draft_email",
    "create_todo",
    "create_calendar",
    "mark_useful",
    "mark_irrelevant",
    "mark_not_interested",
    "mark_too_easy",
    "mark_too_hard",
    "apply_opportunity",
}


class MemoryUpdater:
    def log_event(
        self,
        db: Session,
        user_id: int,
        event_type: str,
        event_target: str = "",
        event_metadata: dict[str, Any] | None = None,
    ) -> UserEvent:
        if event_type not in ALLOWED_EVENT_TYPES:
            raise ValueError(f"Unsupported event_type: {event_type}")
        event = UserEvent(
            user_id=user_id,
            event_type=event_type,
            event_target=event_target,
            event_metadata=dumps(event_metadata or {}),
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return event
