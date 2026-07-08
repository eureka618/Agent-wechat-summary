from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.entities import Opportunity, Recommendation, UserEvent, UserProfile
from app.services.json_utils import dumps, loads_dict


logger = logging.getLogger(__name__)

FEEDBACK_EVENT_TYPES = {
    "mark_useful",
    "mark_irrelevant",
    "mark_not_interested",
    "mark_too_easy",
    "mark_too_hard",
}

BIAS_EVENT_TYPES = FEEDBACK_EVENT_TYPES | {"create_todo", "apply_opportunity"}


class FeedbackService:
    def record_recommendation_feedback(
        self,
        db: Session,
        user_id: int,
        recommendation_id: int,
        event_type: str,
        opportunity_id: int,
        metadata: dict[str, Any] | None = None,
    ) -> UserEvent:
        try:
            if event_type not in FEEDBACK_EVENT_TYPES:
                raise ValueError(f"Unsupported feedback event_type: {event_type}")
            user = db.get(UserProfile, user_id)
            if not user:
                raise ValueError("用户画像不存在")
            recommendation = db.get(Recommendation, recommendation_id)
            if not recommendation or recommendation.user_id != user_id:
                raise ValueError("推荐记录不存在")
            if recommendation.opportunity_id != opportunity_id:
                raise ValueError("推荐记录和机会不匹配")
            opportunity = db.get(Opportunity, opportunity_id)
            if not opportunity:
                raise ValueError("机会不存在")

            event_metadata = {
                **(metadata or {}),
                "source": (metadata or {}).get("source", "recommendation_card"),
                "feedback_type": event_type,
                "feedback_text": str((metadata or {}).get("feedback_text") or "").strip(),
                "user_id": user_id,
                "opportunity_id": opportunity.id,
                "recommendation_id": recommendation.id,
                "opportunity_category": opportunity.category,
                "category": opportunity.category,
                "opportunity_tags": self._keywords(opportunity),
                "tags": self._keywords(opportunity),
                "opportunity_name": opportunity.name,
            }
            event = UserEvent(
                user_id=user_id,
                event_type=event_type,
                event_target=str(recommendation_id),
                event_metadata=dumps(event_metadata),
            )
            db.add(event)
            db.commit()
            db.refresh(event)
            logger.info(
                "feedback recorded user_id=%s recommendation_id=%s opportunity_id=%s event_type=%s",
                user_id,
                recommendation_id,
                opportunity_id,
                event_type,
            )
            return event
        except ValueError:
            db.rollback()
            raise
        except SQLAlchemyError:
            db.rollback()
            logger.exception("record feedback db failure user_id=%s recommendation_id=%s", user_id, recommendation_id)
            raise RuntimeError("反馈记录失败")

    def get_user_feedback_bias(self, db: Session, user_id: int, opportunity: Opportunity) -> float:
        events = (
            db.query(UserEvent)
            .filter(UserEvent.user_id == user_id, UserEvent.event_type.in_(BIAS_EVENT_TYPES))
            .order_by(UserEvent.created_at.desc())
            .limit(50)
            .all()
        )
        if not events:
            return 0.0

        score = 0.0
        current_keywords = set(self._keywords(opportunity))
        for event in events:
            metadata = loads_dict(event.event_metadata)
            category_match = metadata.get("opportunity_category") == opportunity.category or metadata.get("category") == opportunity.category
            event_keywords = set(metadata.get("opportunity_tags") or [])
            keyword_overlap = bool(current_keywords & event_keywords)
            if not category_match and not keyword_overlap:
                continue

            weight = 1.0 if category_match else 0.55
            if event.event_type == "mark_useful":
                score += 0.08 * weight
            elif event.event_type == "mark_not_interested":
                score -= 0.10 * weight
            elif event.event_type == "create_todo":
                score += 0.12 * weight
            elif event.event_type == "apply_opportunity":
                score += 0.15 * weight
            elif event.event_type == "mark_irrelevant":
                score -= 0.06 * weight
            elif event.event_type == "mark_too_easy":
                score += self._difficulty_bias(opportunity, prefer_harder=True) * 0.5 * weight
            elif event.event_type == "mark_too_hard":
                score += self._difficulty_bias(opportunity, prefer_harder=False) * 0.5 * weight

        return round(max(-0.3, min(0.3, score)), 3)

    def _difficulty_bias(self, opportunity: Opportunity, prefer_harder: bool) -> float:
        cost = max(0, min(float(opportunity.cost or 0), 1))
        if prefer_harder:
            return 0.06 if cost >= 0.45 else -0.06
        return 0.06 if cost <= 0.55 else -0.08

    def _keywords(self, opportunity: Opportunity) -> list[str]:
        text = " ".join(
            [
                opportunity.name or "",
                opportunity.category or "",
                opportunity.summary or "",
                opportunity.requirements or "",
                opportunity.target_audience or "",
                opportunity.organizer or "",
            ]
        )
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}|[\u4e00-\u9fff]{2,}", text)
        stopwords = {"机会", "招募", "通知", "报名", "项目", "相关", "官方", "活动", "申请"}
        result = []
        for token in tokens:
            if token not in stopwords and token not in result:
                result.append(token)
        return result[:12]
