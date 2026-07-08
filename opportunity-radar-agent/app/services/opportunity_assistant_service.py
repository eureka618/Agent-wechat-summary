from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.entities import GrowthMemorySnapshot, Opportunity, Recommendation, UserProfile
from app.services.external_search_provider import BochaSearchProvider, ExternalSearchResult
from app.services.growth_memory.memory_manager import GrowthMemoryManager
from app.services.json_utils import loads_list
from app.services.llm_gateway import LLMGateway


class OpportunityAssistantService:
    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self.gateway = gateway or LLMGateway()

    def chat(
        self,
        db: Session,
        user_id: int,
        opportunity_id: int,
        message: str,
        use_web_search: bool = False,
        include_memory: bool = True,
    ) -> dict[str, Any]:
        user = db.get(UserProfile, user_id)
        if not user:
            raise ValueError("用户画像不存在")
        opportunity = db.get(Opportunity, opportunity_id)
        if not opportunity:
            raise ValueError("机会不存在")

        recommendation = (
            db.query(Recommendation)
            .filter(Recommendation.user_id == user_id, Recommendation.opportunity_id == opportunity_id)
            .order_by(Recommendation.created_at.desc())
            .first()
        )
        latest_memory = GrowthMemoryManager().latest_snapshot(db, user_id) if include_memory else None
        profile = self._profile_dict(user)
        opportunity_data = self._opportunity_dict(opportunity)
        recommendation_data = self._recommendation_dict(recommendation)

        should_search = use_web_search or self._needs_web_search(message)
        sources: list[dict[str, Any]] = []
        search_status = "none"
        if should_search:
            search_response = self._search(message, opportunity)
            if search_response["status"] == "success":
                sources = search_response["sources"]
                search_status = "used_bocha" if sources else "failed"
            else:
                search_status = search_response["status"]

        answer = self._answer(
            message=message,
            profile=profile,
            opportunity=opportunity_data,
            recommendation=recommendation_data,
            memories=[self._snapshot_dict(latest_memory)] if latest_memory else [],
            sources=sources,
            used_search=bool(sources),
            search_status=search_status,
        )
        memory_updated = self._maybe_record_preference(
            db=db,
            user=user,
            opportunity=opportunity,
            message=message,
            profile=profile,
        )
        return {
            "answer": answer,
            "used_search": bool(sources),
            "search_status": search_status,
            "sources": sources,
            "memory_updated": memory_updated,
        }

    def _search(self, message: str, opportunity: Opportunity) -> dict[str, Any]:
        settings = get_settings()
        if (settings.web_search_provider or "bocha").lower() != "bocha":
            return {"status": "unavailable", "sources": []}
        query = " ".join(
            item
            for item in [
                opportunity.name,
                opportunity.category,
                opportunity.organizer,
                message,
            ]
            if item
        )
        response = BochaSearchProvider().search(query)
        if response.status == "success":
            return {"status": "success", "sources": [self._source_dict(item) for item in response.results]}
        if response.status == "unavailable":
            return {"status": "unavailable", "sources": []}
        return {"status": "failed", "sources": []}

    def _answer(
        self,
        message: str,
        profile: dict[str, Any],
        opportunity: dict[str, Any],
        recommendation: dict[str, Any],
        memories: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        used_search: bool,
        search_status: str,
    ) -> str:
        system = (
            "你是个人机会顾问。回答要像正常对话，不要展示推荐分数、不要做多维打分条。"
            "用中文，真诚、具体、可执行。所有外部搜索结论只能来自 sources；没有 sources 时不要说你搜索到了。"
        )
        user = f"""
用户问题：{message}

请基于以下上下文回答。结构建议：
1. 适配判断
2. 原因分析
3. 风险提醒
4. 行动建议

如果用户在问类似方向：
- 没有联网结果时，说“基于当前机会特征，可以关注以下类似方向……”
- 有联网结果时，说“根据联网搜索结果，以下方向或机会值得关注……”

用户画像：{json.dumps(profile, ensure_ascii=False)}
当前 opportunity：{json.dumps(opportunity, ensure_ascii=False)}
推荐理由 / 适配判断：{json.dumps(recommendation, ensure_ascii=False)}
成长记忆：{json.dumps(memories, ensure_ascii=False)}
联网搜索状态：{search_status}
联网搜索 sources：{json.dumps(sources[:8], ensure_ascii=False)}
"""
        return self.gateway.chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.35,
        )

    def _maybe_record_preference(
        self,
        db: Session,
        user: UserProfile,
        opportunity: Opportunity,
        message: str,
        profile: dict[str, Any],
    ) -> bool:
        if not self._looks_like_preference_feedback(message):
            return False
        GrowthMemoryManager().log_event(
            db,
            user_id=user.id,
            event_type="assistant_feedback",
            event_target=str(opportunity.id),
            event_metadata={
                "feedback_text": message,
                "feedback_type": "assistant_feedback",
                "opportunity_id": opportunity.id,
                "title": opportunity.name,
                "opportunity_name": opportunity.name,
                "category": opportunity.category,
                "organizer": opportunity.organizer,
            },
        )
        return False

    def _needs_web_search(self, message: str) -> bool:
        text = message.lower()
        keywords = ["类似", "更多", "还有", "更新", "现在", "还能报名", "截止了吗", "联网", "搜索", "找"]
        return any(keyword in text for keyword in keywords)

    def _looks_like_preference_feedback(self, message: str) -> bool:
        keywords = ["我对", "我更", "我不想", "不想做", "更希望", "偏好", "喜欢", "不喜欢", "希望找", "长期", "转正"]
        return any(keyword in message for keyword in keywords)

    def _profile_dict(self, user: UserProfile) -> dict[str, Any]:
        return {
            "姓名": user.name,
            "专业方向": user.major_direction,
            "年级/身份": user.grade_identity,
            "当前目标": loads_list(user.current_goals),
            "技能": loads_list(user.skills),
            "感兴趣领域": loads_list(user.interested_fields),
            "不感兴趣内容": loads_list(user.disliked_contents),
            "详细需求": user.detailed_needs,
            "时间偏好": user.time_preference,
            "地点偏好": user.location_preference,
        }

    def _opportunity_dict(self, opportunity: Opportunity) -> dict[str, Any]:
        return {
            "机会名称": opportunity.name,
            "类别": opportunity.category,
            "截止时间": opportunity.deadline,
            "主办方": opportunity.organizer,
            "地点": opportunity.location,
            "面向人群": opportunity.target_audience,
            "要求": opportunity.requirements,
            "摘要": opportunity.summary,
            "链接": opportunity.link,
            "风险提示": opportunity.risk_flags,
            "核验状态": opportunity.verification_status,
            "可信度": opportunity.credibility_score,
            "风险等级": opportunity.risk_level,
        }

    def _recommendation_dict(self, recommendation: Recommendation | None) -> dict[str, Any]:
        if not recommendation:
            return {}
        return {
            "推荐理由": recommendation.reason,
            "内容概括": recommendation.content_overview,
            "相关性解释": recommendation.relevance_explanation,
            "行动建议": recommendation.action_suggestion,
            "截止提醒": recommendation.deadline_note,
            "风险备注": recommendation.risk_notes,
        }

    def _snapshot_dict(self, memory: GrowthMemorySnapshot) -> dict[str, Any]:
        return {
            "当前阶段": memory.current_stage,
            "近期关注重点": memory.recent_focus,
            "判断标准与偏好变化": memory.preference_changes,
            "整体观察": memory.overall_observation,
            "下一阶段建议": memory.next_stage_advice,
            "更新时间": memory.created_at.isoformat() if memory.created_at else "",
        }

    def _source_dict(self, item: ExternalSearchResult) -> dict[str, Any]:
        return {
            "title": item.title,
            "url": item.url,
            "snippet": item.snippet,
            "source": item.source,
        }
