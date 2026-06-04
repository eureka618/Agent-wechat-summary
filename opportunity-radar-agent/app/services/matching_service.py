import re
from datetime import datetime

from sqlalchemy.orm import Session, joinedload

from app.models.entities import Opportunity, Recommendation, UserProfile
from app.services.json_utils import loads_list


GOAL_CATEGORY_WEIGHT = {
    "找实习": {"实习": 1.0, "课程": 0.45, "讲座": 0.35, "竞赛": 0.4},
    "科研入门": {"科研": 1.0, "实验室招募": 0.9, "讲座": 0.5, "课程": 0.55},
    "竞赛加分": {"竞赛": 1.0, "课程": 0.45, "讲座": 0.35},
    "保研": {"夏令营": 1.0, "科研": 0.9, "竞赛": 0.75, "实验室招募": 0.65, "讲座": 0.4},
    "申请项目": {"夏令营": 0.85, "科研": 0.8, "课程": 0.7, "奖学金": 0.65},
}


class MatchingService:
    def generate_for_user(self, db: Session, user_id: int) -> list[Recommendation]:
        user = db.get(UserProfile, user_id)
        if not user:
            raise ValueError("用户画像不存在")
        db.query(Recommendation).filter(Recommendation.user_id == user_id).delete()
        opportunities = db.query(Opportunity).all()
        recommendations = [self._score(user, opportunity) for opportunity in opportunities]
        recommendations.sort(key=lambda item: item.total_score, reverse=True)
        for recommendation in recommendations:
            db.add(recommendation)
        db.commit()
        return (
            db.query(Recommendation)
            .options(joinedload(Recommendation.opportunity))
            .filter(Recommendation.user_id == user_id)
            .order_by(Recommendation.total_score.desc())
            .all()
        )

    def _score(self, user: UserProfile, opportunity: Opportunity) -> Recommendation:
        goals = loads_list(user.current_goals)
        skills = loads_list(user.skills)
        interests = loads_list(user.interested_fields)
        dislikes = loads_list(user.disliked_contents)
        haystack = " ".join(
            [
                opportunity.name,
                opportunity.category,
                opportunity.summary,
                opportunity.requirements,
                opportunity.target_audience,
                opportunity.organizer,
            ]
        ).lower()

        relevance = self._relevance(goals, skills, interests, dislikes, user.major_direction, haystack, opportunity.category)
        urgency = self._urgency(opportunity.deadline)
        benefit = max(0, min(opportunity.benefit, 1)) * 100
        cost = (1 - max(0, min(opportunity.cost, 1))) * 100
        credibility = max(0, min(opportunity.credibility, 1)) * 100
        location_bonus = self._location_bonus(user.location_preference, opportunity.location)
        time_bonus = self._time_bonus(user.time_preference, opportunity.event_time)

        total = relevance * 0.4 + urgency * 0.2 + benefit * 0.2 + cost * 0.1 + credibility * 0.1
        total = min(100, max(0, total + location_bonus + time_bonus))

        return Recommendation(
            user_id=user.id,
            opportunity_id=opportunity.id,
            relevance_score=round(relevance, 1),
            urgency_score=round(urgency, 1),
            benefit_score=round(benefit, 1),
            cost_score=round(cost, 1),
            credibility_score=round(credibility, 1),
            total_score=round(total, 1),
            reason=self._reason(user, opportunity, relevance, urgency),
            action_suggestion=self._action_suggestion(opportunity, total, urgency),
        )

    def _relevance(
        self,
        goals: list[str],
        skills: list[str],
        interests: list[str],
        dislikes: list[str],
        major: str,
        haystack: str,
        category: str,
    ) -> float:
        score = 35.0
        for goal in goals:
            score += GOAL_CATEGORY_WEIGHT.get(goal, {}).get(category, 0.15) * 25
            if goal.lower() in haystack:
                score += 8
        for keyword in [major, *skills, *interests]:
            if keyword and keyword.lower() in haystack:
                score += 8
        for keyword in dislikes:
            if keyword and keyword.lower() in haystack:
                score -= 25
        return min(100, max(0, score))

    def _urgency(self, deadline: str) -> float:
        parsed = self._parse_date(deadline)
        if not parsed:
            return 50
        days = (parsed - datetime.utcnow()).days
        if days < 0:
            return 10
        if days <= 3:
            return 100
        if days <= 7:
            return 88
        if days <= 14:
            return 72
        if days <= 30:
            return 55
        return 35

    def _parse_date(self, text: str) -> datetime | None:
        if not text:
            return None
        patterns = [
            r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})",
            r"(\d{1,2})[-/.月](\d{1,2})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            groups = match.groups()
            if len(groups) == 3:
                year, month, day = groups
            else:
                year = str(datetime.utcnow().year)
                month, day = groups
            try:
                return datetime(int(year), int(month), int(day))
            except ValueError:
                return None
        return None

    def _location_bonus(self, preference: str, location: str) -> float:
        if not preference or not location:
            return 0
        if preference in location or location in preference:
            return 4
        if "线上" in preference and "线上" in location:
            return 4
        return -3 if "线下" in location and "线上" in preference else 0

    def _time_bonus(self, preference: str, event_time: str) -> float:
        if not preference or not event_time:
            return 0
        return 3 if any(word in event_time for word in preference.split()) else 0

    def _reason(self, user: UserProfile, opportunity: Opportunity, relevance: float, urgency: float) -> str:
        goals = "、".join(loads_list(user.current_goals)) or "当前目标"
        parts = [f"与「{goals}」和「{user.major_direction or '专业方向'}」的匹配度为 {relevance:.0f}。"]
        if urgency >= 80:
            parts.append("截止时间较近，建议优先处理。")
        if opportunity.credibility >= 0.8:
            parts.append("主办方/来源信息较完整，可信度较高。")
        return "".join(parts)

    def _action_suggestion(self, opportunity: Opportunity, total: float, urgency: float) -> str:
        actions = []
        if total >= 75:
            actions.append("建议报名或申请")
        else:
            actions.append("建议先收藏并补充了解")
        if urgency >= 80:
            actions.append("加入日历提醒")
        if opportunity.category in {"实习", "实验室招募", "科研"}:
            actions.append("准备简历/项目经历")
        if opportunity.category in {"科研", "实验室招募", "夏令营"}:
            actions.append("进一步搜索导师或实验室背景")
        return "；".join(actions)
