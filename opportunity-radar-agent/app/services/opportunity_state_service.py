from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.entities import OpportunityState, Recommendation


STATE_PRIORITY = {
    "new": 0,
    "recommended": 1,
    "verified": 2,
    "saved": 3,
    "todo_created": 4,
    "calendar_created": 5,
    "email_drafted": 6,
    "applied": 7,
    "archived": 8,
}

ACTION_STATE_MAP = {
    "verify": "verified",
    "todo": "todo_created",
    "calendar": "calendar_created",
}


class OpportunityStateService:
    def get_state(self, db: Session, user_id: int, opportunity_id: int) -> OpportunityState:
        state = (
            db.query(OpportunityState)
            .filter(OpportunityState.user_id == user_id, OpportunityState.opportunity_id == opportunity_id)
            .first()
        )
        if state:
            return state
        state = OpportunityState(user_id=user_id, opportunity_id=opportunity_id, state="new")
        db.add(state)
        db.flush()
        return state

    def advance_state(self, db: Session, user_id: int, opportunity_id: int, target_state: str) -> OpportunityState:
        if target_state not in STATE_PRIORITY:
            raise ValueError(f"Unsupported opportunity_state: {target_state}")
        state = self.get_state(db, user_id, opportunity_id)
        current_rank = STATE_PRIORITY.get(state.state, 0)
        target_rank = STATE_PRIORITY[target_state]
        if target_rank > current_rank:
            state.state = target_state
            state.updated_at = datetime.utcnow()
            self._sync_recommendations(db, user_id, opportunity_id, target_state)
        elif not state.state:
            state.state = target_state
            state.updated_at = datetime.utcnow()
        return state

    def advance_for_action(self, db: Session, user_id: int, opportunity_id: int, action: str) -> OpportunityState | None:
        target_state = ACTION_STATE_MAP.get(action)
        if not target_state:
            return None
        return self.advance_state(db, user_id, opportunity_id, target_state)

    def _sync_recommendations(self, db: Session, user_id: int, opportunity_id: int, state: str) -> None:
        (
            db.query(Recommendation)
            .filter(Recommendation.user_id == user_id, Recommendation.opportunity_id == opportunity_id)
            .update({Recommendation.opportunity_state: state}, synchronize_session=False)
        )
