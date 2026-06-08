from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.dto import (
    OpportunityActionRequest,
    OpportunityActionResponse,
    OpportunityStateOut,
    OpportunityStateRequest,
)
from app.services.opportunity_agent import OpportunityAgent
from app.services.opportunity_state_service import OpportunityStateService

router = APIRouter(prefix="/opportunities", tags=["opportunity-actions"])


@router.get("/{opportunity_id}/state", response_model=OpportunityStateOut)
def get_opportunity_state(opportunity_id: int, user_id: int, db: Session = Depends(get_db)):
    state = OpportunityStateService().get_state(db, user_id, opportunity_id)
    db.commit()
    db.refresh(state)
    return state


@router.post("/{opportunity_id}/state", response_model=OpportunityStateOut)
def update_opportunity_state(
    opportunity_id: int,
    payload: OpportunityStateRequest,
    db: Session = Depends(get_db),
):
    try:
        state = OpportunityStateService().advance_state(db, payload.user_id, opportunity_id, payload.state)
        db.commit()
        db.refresh(state)
        return state
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{opportunity_id}/actions", response_model=OpportunityActionResponse)
def run_opportunity_action(
    opportunity_id: int,
    payload: OpportunityActionRequest,
    db: Session = Depends(get_db),
):
    try:
        return OpportunityAgent().run_opportunity_action(
            db=db,
            user_id=payload.user_id,
            opportunity_id=opportunity_id,
            action=payload.action,
            extra_params=payload.extra_params,
        )
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{opportunity_id}/actions/verify", response_model=OpportunityActionResponse)
def verify_opportunity(opportunity_id: int, payload: OpportunityActionRequest, db: Session = Depends(get_db)):
    payload.action = "verify"
    return run_opportunity_action(opportunity_id, payload, db)


@router.post("/{opportunity_id}/actions/enrich", response_model=OpportunityActionResponse)
def enrich_opportunity(opportunity_id: int, payload: OpportunityActionRequest, db: Session = Depends(get_db)):
    payload.action = "enrich"
    return run_opportunity_action(opportunity_id, payload, db)


@router.post("/{opportunity_id}/actions/calendar", response_model=OpportunityActionResponse)
def create_calendar(opportunity_id: int, payload: OpportunityActionRequest, db: Session = Depends(get_db)):
    payload.action = "calendar"
    return run_opportunity_action(opportunity_id, payload, db)


@router.post("/{opportunity_id}/actions/email", response_model=OpportunityActionResponse)
def draft_email(opportunity_id: int, payload: OpportunityActionRequest, db: Session = Depends(get_db)):
    payload.action = "email"
    return run_opportunity_action(opportunity_id, payload, db)


@router.post("/{opportunity_id}/actions/todo", response_model=OpportunityActionResponse)
def create_todo(opportunity_id: int, payload: OpportunityActionRequest, db: Session = Depends(get_db)):
    payload.action = "todo"
    return run_opportunity_action(opportunity_id, payload, db)
