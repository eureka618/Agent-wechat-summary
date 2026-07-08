import re
from datetime import datetime

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.models.entities import Opportunity, Recommendation, UserEvent, UserProfile
from app.services.feedback_service import FeedbackService
from app.services.growth_memory.memory_manager import GrowthMemoryManager
from app.services.json_utils import dumps, loads_dict, loads_list
from app.services.llm_service import LLMService
from app.services.opportunity_state_service import OpportunityStateService


GOAL_CATEGORY_WEIGHT = {
    "找实习": {"实习": 1.0, "课程": 0.45, "讲座": 0.35, "竞赛": 0.4},
    "科研入门": {"科研": 1.0, "实验室招募": 0.9, "讲座": 0.5, "课程": 0.55},
    "竞赛加分": {"竞赛": 1.0, "课程": 0.45, "讲座": 0.35},
    "保研": {"夏令营": 1.0, "科研": 0.9, "竞赛": 0.75, "实验室招募": 0.65, "讲座": 0.4},
    "申请项目": {"夏令营": 0.85, "科研": 0.8, "课程": 0.7, "奖学金": 0.65},
}

logger = logging.getLogger(__name__)

# Internal ranking weights. The UI intentionally hides numeric scores, so this
# formula can evolve without making the demo feel like a spreadsheet to users.
SCORE_WEIGHTS = {
    "relevance": 0.55,
    "urgency": 0.05,
    "benefit": 0.35,
    "cost": 0.05,
}


class MatchingService:
    def __init__(self) -> None:
        self.llm = LLMService()

    def generate_for_user(self, db: Session, user_id: int, force: bool = True) -> list[Recommendation]:
        try:
            user = db.get(UserProfile, user_id)
            if not user:
                raise ValueError("用户画像不存在")
            existing_query = (
                db.query(Recommendation)
                .join(Opportunity, Recommendation.opportunity_id == Opportunity.id)
                .options(joinedload(Recommendation.opportunity))
                .filter(Recommendation.user_id == user_id)
                .order_by(Recommendation.total_score.desc())
            )
            if not force:
                existing = existing_query.all()
                if existing:
                    return existing

            opportunities = db.query(Opportunity).options(joinedload(Opportunity.article)).all()
            if not opportunities:
                logger.info("recommendations empty: no opportunities user_id=%s", user_id)
                return existing_query.all()

            memory = GrowthMemoryManager()
            feedback = FeedbackService()
            state_service = OpportunityStateService()
            recommendations = []
            for opportunity in opportunities:
                try:
                    state = state_service.get_state(db, user_id, opportunity.id)
                    relevant_memory = [item.summary for item in memory.retrieve_relevant_memory(db, user_id, opportunity)]
                    negative_feedback_count = self._recent_negative_feedback_count(db, user_id, opportunity)
                    feedback_bias = feedback.get_user_feedback_bias(db, user_id, opportunity)
                    db.rollback()
                    recommendations.append(
                        self._score(
                            user,
                            opportunity,
                            state.state if state.state and state.state != "new" else "recommended",
                            relevant_memory,
                            negative_feedback_count,
                            feedback_bias,
                        )
                    )
                except Exception as exc:
                    db.rollback()
                    logger.exception(
                        "recommendation opportunity skipped user_id=%s opportunity_id=%s error_type=%s",
                        user_id,
                        opportunity.id,
                        type(exc).__name__,
                    )
            recommendations.sort(key=lambda item: item.total_score, reverse=True)
            if not recommendations:
                logger.warning("recommendations refresh produced no rows; keep previous user_id=%s", user_id)
                return existing_query.all()
            db.query(Recommendation).filter(Recommendation.user_id == user_id).delete(synchronize_session=False)
            for recommendation in recommendations:
                db.add(recommendation)
                state_service.advance_state(db, user_id, recommendation.opportunity_id, "recommended")
            db.commit()
            return (
                db.query(Recommendation)
                .join(Opportunity, Recommendation.opportunity_id == Opportunity.id)
                .options(joinedload(Recommendation.opportunity))
                .filter(Recommendation.user_id == user_id)
                .order_by(Recommendation.total_score.desc())
                .all()
            )
        except ValueError:
            db.rollback()
            raise
        except SQLAlchemyError:
            db.rollback()
            logger.exception("generate_for_user db failure user_id=%s", user_id)
            raise
        except Exception:
            db.rollback()
            logger.exception("generate_for_user failed user_id=%s", user_id)
            raise

    def _score(
        self,
        user: UserProfile,
        opportunity: Opportunity,
        opportunity_state: str,
        relevant_memory: list[str] | None = None,
        negative_feedback_count: int = 0,
        feedback_bias: float = 0.0,
    ) -> Recommendation:
        relevant_memory = relevant_memory or []
        goals = loads_list(user.current_goals)
        skills = loads_list(user.skills)
        interests = loads_list(user.interested_fields)
        dislikes = loads_list(user.disliked_contents)
        detailed_keywords = self._extract_need_keywords(user.detailed_needs)
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

        relevance = self._relevance(
            goals,
            skills,
            interests,
            detailed_keywords,
            dislikes,
            user.major_direction,
            haystack,
            opportunity.category,
        )
        deadline_urgency, deadline_note, days_until_deadline = self._deadline_info(opportunity.deadline)
        urgency = self._urgency_from_deadline(deadline_urgency)
        benefit = max(0, min(opportunity.benefit, 1)) * 100
        cost = (1 - max(0, min(opportunity.cost, 1))) * 100
        credibility = self._credibility_score(opportunity)
        location_bonus = self._location_bonus(user.location_preference, opportunity.location)
        time_bonus = self._time_bonus(user.time_preference, opportunity.event_time)
        base_scores = {
            "relevance_score": relevance,
            "benefit_score": benefit,
            "urgency_score": urgency,
            "cost_score": cost,
        }
        try:
            analysis = self.llm.analyze_recommendation(
                profile=self._profile_payload(user),
                opportunity=self._opportunity_payload(opportunity),
                base_scores=base_scores,
            )
        except Exception as exc:
            logger.exception(
                "recommendation llm fallback opportunity_id=%s error_type=%s",
                opportunity.id,
                type(exc).__name__,
            )
            analysis = {
                "relevance_score": relevance,
                "benefit_score": benefit,
                "content_overview": self._fallback_overview(opportunity),
                "relevance_explanation": self._reason(user, opportunity, relevance, urgency, relevant_memory, deadline_urgency),
            }
        relevance = float(analysis.get("relevance_score", relevance))
        benefit = float(analysis.get("benefit_score", benefit))

        total = (
            relevance * SCORE_WEIGHTS["relevance"]
            + urgency * SCORE_WEIGHTS["urgency"]
            + benefit * SCORE_WEIGHTS["benefit"]
            + cost * SCORE_WEIGHTS["cost"]
        )
        total += self._verification_adjustment(opportunity)
        if deadline_urgency == "urgent" and relevance >= 70:
            total += 8
        elif deadline_urgency == "soon" and relevance >= 70:
            total += 4
        elif deadline_urgency == "expired":
            total -= 35
        feedback_delta = feedback_bias * 30
        total += feedback_delta
        total = min(100, max(0, total + location_bonus + time_bonus))
        risk_notes = self._risk_notes(
            user=user,
            opportunity=opportunity,
            relevance=relevance,
            cost_score=cost,
            location_bonus=location_bonus,
            time_bonus=time_bonus,
            deadline_urgency=deadline_urgency,
            negative_feedback_count=negative_feedback_count,
            feedback_bias=feedback_bias,
        )
        reason = analysis.get("relevance_explanation", "") or self._reason(
            user, opportunity, relevance, urgency, relevant_memory, deadline_urgency
        )
        if feedback_bias:
            direction = "加权" if feedback_bias > 0 else "降权"
            reason = f"{reason}（已根据近期反馈对同类机会{direction} {abs(feedback_delta):.1f} 分。）"

        return Recommendation(
            user_id=user.id,
            opportunity_id=opportunity.id,
            relevance_score=round(relevance, 1),
            urgency_score=round(urgency, 1),
            benefit_score=round(benefit, 1),
            cost_score=round(cost, 1),
            credibility_score=round(credibility, 1),
            total_score=round(total, 1),
            reason=reason,
            content_overview=str(analysis.get("content_overview", "")),
            relevance_explanation=str(analysis.get("relevance_explanation", "")),
            risk_notes=dumps(risk_notes),
            anti_recommendation_reason=self._anti_recommendation_reason(risk_notes),
            action_suggestion=self._action_suggestion(opportunity, total, urgency),
            opportunity_state=opportunity_state,
            deadline=opportunity.deadline or "",
            deadline_urgency=deadline_urgency,
            deadline_note=deadline_note,
        )

    def _relevance(
        self,
        goals: list[str],
        skills: list[str],
        interests: list[str],
        detailed_keywords: list[str],
        dislikes: list[str],
        major: str,
        haystack: str,
        category: str,
    ) -> float:
        score = 35.0
        for goal in goals:
            score += GOAL_CATEGORY_WEIGHT.get(goal, {}).get(category, 0.08) * 25
            if goal.lower() in haystack:
                score += 14
        for keyword in [major, *skills, *interests]:
            if keyword and keyword.lower() in haystack:
                score += 8
        for keyword in detailed_keywords:
            if keyword and keyword.lower() in haystack:
                score += 6
        for keyword in dislikes:
            if keyword and keyword.lower() in haystack:
                score -= 25
        return min(100, max(0, score))

    def _extract_need_keywords(self, text: str) -> list[str]:
        if not text:
            return []
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}|[\u4e00-\u9fff]{2,}", text)
        stopwords = {"希望", "最好", "比较", "相关", "机会", "方向", "帮助", "可以", "能够", "需要", "优先"}
        keywords = []
        for token in tokens:
            token = token.strip()
            if token and token not in stopwords and token not in keywords:
                keywords.append(token)
        return keywords[:20]

    def _urgency(self, deadline: str) -> float:
        deadline_urgency, _, _ = self._deadline_info(deadline)
        return self._urgency_from_deadline(deadline_urgency)

    def _urgency_from_deadline(self, deadline_urgency: str) -> float:
        if deadline_urgency == "expired":
            return 5
        if deadline_urgency == "urgent":
            return 100
        if deadline_urgency == "soon":
            return 88
        if deadline_urgency == "normal":
            return 55
        return 45

    def _deadline_info(self, deadline: str) -> tuple[str, str, int | None]:
        parsed = self._parse_date(deadline)
        if not parsed:
            return "unknown", "未识别到明确截止时间，建议先核验原文或报名入口。", None
        days = (parsed.date() - datetime.utcnow().date()).days
        if days < 0:
            return "expired", f"已在 {abs(days)} 天前截止，不建议作为高优先级行动。", days
        if days <= 3:
            return "urgent", f"距离截止还有 {days} 天，建议今天完成报名信息确认。", days
        if days <= 7:
            return "soon", f"距离截止还有 {days} 天，建议本周内确认材料和入口。", days
        return "normal", f"距离截止还有 {days} 天，可以按匹配度安排准备节奏。", days

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

    def _credibility_score(self, opportunity: Opportunity) -> float:
        if opportunity.verification_status and opportunity.credibility_score is not None:
            return max(0, min(float(opportunity.credibility_score), 100))
        return max(0, min(opportunity.credibility, 1)) * 100

    def _verification_adjustment(self, opportunity: Opportunity) -> float:
        status = opportunity.verification_status
        risk = opportunity.risk_level
        if status == "verified" and (opportunity.credibility_score or 0) >= 80 and risk == "low":
            return 5
        if status == "uncertain":
            return -2
        if status == "suspicious" or risk == "high":
            return -18
        if risk == "medium":
            return -6
        return 0

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

    def _profile_payload(self, user: UserProfile) -> dict:
        return {
            "name": user.name,
            "major_direction": user.major_direction,
            "grade_identity": user.grade_identity,
            "current_goals": loads_list(user.current_goals),
            "skills": loads_list(user.skills),
            "interested_fields": loads_list(user.interested_fields),
            "disliked_contents": loads_list(user.disliked_contents),
            "detailed_needs": user.detailed_needs,
            "time_preference": user.time_preference,
            "location_preference": user.location_preference,
        }

    def _opportunity_payload(self, opportunity: Opportunity) -> dict:
        article = getattr(opportunity, "article", None)
        article_content = getattr(article, "content", "") or ""
        return {
            "name": opportunity.name,
            "category": opportunity.category,
            "organizer": opportunity.organizer,
            "event_time": opportunity.event_time,
            "deadline": opportunity.deadline,
            "location": opportunity.location,
            "target_audience": opportunity.target_audience,
            "requirements": opportunity.requirements,
            "summary": opportunity.summary,
            "link": opportunity.link,
            "benefit": opportunity.benefit,
            "cost": opportunity.cost,
            "credibility": opportunity.credibility,
            "article_title": getattr(article, "title", "") or "",
            "article_content_excerpt": article_content[:3000],
        }

    def _fallback_overview(self, opportunity: Opportunity) -> str:
        text = " ".join(part for part in [opportunity.summary, opportunity.requirements, opportunity.target_audience] if part)
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            text = f"{opportunity.name} 是一个{opportunity.category or '机会'}，建议先阅读原文确认要求、入口和截止时间。"
        return text[:500]

    def _reason(
        self,
        user: UserProfile,
        opportunity: Opportunity,
        relevance: float,
        urgency: float,
        relevant_memory: list[str],
        deadline_urgency: str,
    ) -> str:
        goals = loads_list(user.current_goals)
        focus = "、".join(goals[:2]) if goals else "当前目标"
        parts = []
        if relevant_memory:
            first = relevant_memory[0].replace("\n", " ")
            if "暂无足够" not in first:
                parts.append(f"结合近期行为洞察：{first} ")
        if opportunity.category in {"科研", "实验室招募"}:
            parts.append(f"相关性：它更像一个能补足{focus}经历的机会，适合用来积累项目、导师沟通或研究入门材料。")
        elif opportunity.category == "实习":
            parts.append("相关性：它偏向真实业务经历，适合把技能栈转化成简历上更具体的项目表达。")
        elif opportunity.category == "竞赛":
            parts.append("相关性：它适合作为作品集或竞赛经历补充，关键是尽早确认队友和可交付 Demo。")
        elif opportunity.category == "夏令营":
            parts.append("相关性：它和升学申请节奏相关，适合提前准备成绩、简历和科研/竞赛证明材料。")
        else:
            parts.append(f"相关性：它和你当前的{focus}方向有一定关联，可以作为备选机会关注。")
        if deadline_urgency == "unknown":
            parts.append("截止时间暂不明确，需要先确认原文信息。")
        elif deadline_urgency == "expired":
            parts.append("但它看起来已经过期，因此只适合作为背景参考。")
        if opportunity.credibility >= 0.8:
            parts.append("来源信息相对完整，可信度更高。")
        if relevance < 55:
            parts.append("不过它和你的核心需求不是完全贴合，可以降低优先级。")
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

    def _risk_notes(
        self,
        user: UserProfile,
        opportunity: Opportunity,
        relevance: float,
        cost_score: float,
        location_bonus: float,
        time_bonus: float,
        deadline_urgency: str,
        negative_feedback_count: int,
        feedback_bias: float,
    ) -> list[str]:
        risks = []
        if deadline_urgency == "unknown":
            risks.append("截止时间未知")
        elif deadline_urgency == "expired":
            risks.append("机会已过期，不建议作为当前行动优先项")
        if opportunity.verification_status != "verified":
            risks.append("主办方可信度未核验")
        if relevance < 55:
            risks.append("机会类型和用户目标弱相关")
        if cost_score < 35:
            risks.append("准备门槛可能偏高，需要确认时间和能力投入")
        elif cost_score > 90:
            risks.append("门槛可能偏低，成长收益需要进一步核验")
        if location_bonus < 0:
            risks.append("地点可能与用户偏好不匹配")
        if user.time_preference and opportunity.event_time and time_bonus <= 0:
            risks.append("时间安排可能与用户偏好不匹配")
        content = " ".join([opportunity.summary, opportunity.requirements, opportunity.link, opportunity.registration_url])
        if self._looks_promotional_or_incomplete(content):
            risks.append("文章内容偏宣传，缺少报名方式或明确行动入口")
        if negative_feedback_count >= 2:
            risks.append("与用户近期负反馈方向相似")
        if feedback_bias < 0:
            risks.append("已根据近期反馈降低同类机会权重")
        elif feedback_bias > 0:
            risks.append("已根据近期正反馈提高同类机会权重")
        if not risks:
            return ["暂无明显风险，但建议核验原文链接和截止时间。"]
        return risks

    def _anti_recommendation_reason(self, risk_notes: list[str]) -> str:
        default = "暂无明显风险，但建议核验原文链接和截止时间。"
        meaningful = [item for item in risk_notes if item != default]
        if not meaningful:
            return default
        return "；".join(meaningful[:3])

    def _looks_promotional_or_incomplete(self, text: str) -> bool:
        has_action_entry = any(word in text for word in ["报名", "申请", "链接", "入口", "http", "邮箱", "@"])
        promotional_words = ["重磅", "限时", "名额有限", "保offer", "保录取", "零基础高薪", "速成"]
        return (not has_action_entry) or any(word in text for word in promotional_words)

    def _recent_negative_feedback_count(self, db: Session, user_id: int, opportunity: Opportunity) -> int:
        negative_types = {"mark_irrelevant", "mark_not_interested", "mark_too_easy", "mark_too_hard"}
        events = (
            db.query(UserEvent)
            .filter(UserEvent.user_id == user_id, UserEvent.event_type.in_(negative_types))
            .order_by(UserEvent.created_at.desc())
            .limit(20)
            .all()
        )
        count = 0
        for event in events:
            metadata = loads_dict(event.event_metadata)
            if metadata.get("category") == opportunity.category:
                count += 1
        return count
