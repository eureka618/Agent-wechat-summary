from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
import logging

from app.core.database import get_db
from app.models.entities import Opportunity, UserEvent, UserProfile
from app.schemas.dto import GrowthMemoryResponse, UserEventCreate, UserEventOut
from app.services.growth_memory.memory_manager import GrowthMemoryManager
from app.services.opportunity_state_service import OpportunityStateService

router = APIRouter(prefix="/memory", tags=["growth-memory"])
logger = logging.getLogger(__name__)


@router.post("/events", response_model=UserEventOut)
def log_user_event(payload: UserEventCreate, db: Session = Depends(get_db)) -> UserEvent:
    try:
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
    except Exception as exc:
        logger.exception("log user event failed user_id=%s event_type=%s", payload.user_id, payload.event_type)
        raise HTTPException(status_code=500, detail="用户事件记录失败") from exc


@router.get("/events/{user_id}", response_model=list[UserEventOut])
def list_user_events(user_id: int, limit: int = 30, db: Session = Depends(get_db)) -> list[UserEvent]:
    try:
        return (
            db.query(UserEvent)
            .filter(UserEvent.user_id == user_id)
            .order_by(UserEvent.created_at.desc())
            .limit(limit)
            .all()
        )
    except Exception as exc:
        logger.exception("list user events failed user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="用户事件读取失败") from exc


@router.get("/{user_id}/latest", response_model=GrowthMemoryResponse)
def get_latest_growth_memory(user_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        if not db.get(UserProfile, user_id):
            raise HTTPException(status_code=404, detail="用户画像不存在")
        snapshot = GrowthMemoryManager().latest_snapshot(db, user_id)
        return {"success": True, "updated": False, "message": "", "data": snapshot}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("get latest growth memory failed user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="成长记忆读取失败") from exc


@router.post("/{user_id}/generate", response_model=GrowthMemoryResponse)
def generate_growth_memory(user_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        return GrowthMemoryManager().generate_snapshot(db, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.exception("generate growth memory failed user_id=%s", user_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("generate growth memory failed user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="成长记忆生成失败，请稍后重试。") from exc
