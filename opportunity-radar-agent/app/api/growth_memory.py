from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.entities import MemoryReflection, Opportunity, UserEvent
from app.schemas.dto import GenerateReflectionRequest, MemoryReflectionOut, UserEventCreate, UserEventOut
from app.services.growth_memory.memory_manager import GrowthMemoryManager
from app.services.opportunity_state_service import OpportunityStateService

router = APIRouter(prefix="/memory", tags=["growth-memory"])


@router.post("/events", response_model=UserEventOut)
def log_user_event(payload: UserEventCreate, db: Session = Depends(get_db)) -> UserEvent:
    metadata = dict(payload.event_metadata)
    if payload.event_target and payload.event_target.isdigit() and "opportunity_id" not in metadata:
        opportunity = db.get(Opportunity, int(payload.event_target))
        if opportunity:
            metadata.update(
                {
                    "opportunity_id": opportunity.id,
                    "title": opportunity.name,
                    "category": opportunity.category,
                    "organizer": opportunity.organizer,
                    "deadline": opportunity.deadline,
                }
            )
    try:
        event = GrowthMemoryManager().log_event(
            db,
            user_id=payload.user_id,
            event_type=payload.event_type,
            event_target=payload.event_target,
            event_metadata=metadata,
        )
        if payload.event_type == "apply_opportunity" and payload.event_target.isdigit():
            OpportunityStateService().advance_state(db, payload.user_id, int(payload.event_target), "applied")
            db.commit()
        return event
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/events/{user_id}", response_model=list[UserEventOut])
def list_user_events(user_id: int, limit: int = 30, db: Session = Depends(get_db)) -> list[UserEvent]:
    return (
        db.query(UserEvent)
        .filter(UserEvent.user_id == user_id)
        .order_by(UserEvent.created_at.desc())
        .limit(limit)
        .all()
    )


@router.post("/reflections/generate", response_model=list[MemoryReflectionOut])
def generate_reflections(payload: GenerateReflectionRequest, db: Session = Depends(get_db)) -> list[MemoryReflection]:
    return GrowthMemoryManager().generate_reflection(db, payload.user_id)


@router.get("/reflections/{user_id}", response_model=list[MemoryReflectionOut])
def list_reflections(user_id: int, db: Session = Depends(get_db)) -> list[MemoryReflection]:
    return (
        db.query(MemoryReflection)
        .filter(MemoryReflection.user_id == user_id)
        .order_by(MemoryReflection.updated_at.desc())
        .all()
    )
