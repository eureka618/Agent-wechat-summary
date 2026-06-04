from sqlalchemy.orm import Session, joinedload

from app.models.entities import Recommendation, Summary, UserProfile
from app.services.json_utils import loads_list
from app.services.llm_service import LLMService


class SummaryService:
    def __init__(self) -> None:
        self.llm = LLMService()

    def generate(self, db: Session, user_id: int, period: str = "daily") -> Summary:
        user = db.get(UserProfile, user_id)
        if not user:
            raise ValueError("用户画像不存在")
        recommendations = (
            db.query(Recommendation)
            .options(joinedload(Recommendation.opportunity))
            .filter(Recommendation.user_id == user_id)
            .order_by(Recommendation.total_score.desc())
            .limit(10)
            .all()
        )
        profile = {
            "name": user.name,
            "major_direction": user.major_direction,
            "grade_identity": user.grade_identity,
            "current_goals": loads_list(user.current_goals),
            "skills": loads_list(user.skills),
            "interested_fields": loads_list(user.interested_fields),
            "disliked_contents": loads_list(user.disliked_contents),
            "time_preference": user.time_preference,
            "location_preference": user.location_preference,
        }
        payload = [
            {
                "name": rec.opportunity.name,
                "category": rec.opportunity.category,
                "deadline": rec.opportunity.deadline,
                "total_score": rec.total_score,
                "reason": rec.reason,
                "action_suggestion": rec.action_suggestion,
            }
            for rec in recommendations
        ]
        content = self.llm.summarize_recommendations(profile, payload)
        summary = Summary(user_id=user_id, period=period, content=content)
        db.add(summary)
        db.commit()
        db.refresh(summary)
        return summary
