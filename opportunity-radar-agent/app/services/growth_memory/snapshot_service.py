from __future__ import annotations

import json
import logging
import re
import threading
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.entities import GrowthMemorySnapshot, Opportunity, UserEvent, UserProfile
from app.services.json_utils import dumps, loads_dict, loads_list
from app.services.llm_gateway import LLMGateway


logger = logging.getLogger(__name__)

PROMPT_VERSION = "growth_memory_v2"
ANALYSIS_EVENT_LIMIT = 100
HISTORY_LIMIT = 3
REQUIRED_FIELDS = [
    "current_stage",
    "recent_focus",
    "preference_changes",
    "overall_observation",
    "next_stage_advice",
]

STRONG_ACTION_EVENTS = {"apply_opportunity", "create_calendar", "create_todo", "draft_email"}
POSITIVE_EVENTS = {"save_opportunity", "mark_useful", "verify_opportunity"}
WEAK_EVENTS = {"view_opportunity", "view_source_article"}
NEGATIVE_EVENTS = {"mark_not_interested", "mark_irrelevant", "mark_too_easy", "mark_too_hard"}

_USER_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class GrowthMemorySnapshotService:
    def __init__(self, gateway: LLMGateway | None = None) -> None:
        self.gateway = gateway or LLMGateway()

    def latest(self, db: Session, user_id: int) -> GrowthMemorySnapshot | None:
        return (
            db.query(GrowthMemorySnapshot)
            .filter(GrowthMemorySnapshot.user_id == user_id)
            .order_by(GrowthMemorySnapshot.created_at.desc())
            .first()
        )

    def generate(self, db: Session, user_id: int) -> dict[str, Any]:
        lock = self._lock_for_user(user_id)
        if not lock.acquire(blocking=False):
            latest = self.latest(db, user_id)
            return {
                "success": True,
                "updated": False,
                "message": "成长记忆正在生成中，请稍后再试。",
                "data": latest,
            }
        try:
            return self._generate_locked(db, user_id)
        finally:
            lock.release()

    def _generate_locked(self, db: Session, user_id: int) -> dict[str, Any]:
        user = db.get(UserProfile, user_id)
        if not user:
            raise ValueError("用户画像不存在")

        events = (
            db.query(UserEvent)
            .filter(UserEvent.user_id == user_id)
            .order_by(UserEvent.created_at.desc())
            .limit(ANALYSIS_EVENT_LIMIT)
            .all()
        )
        latest = self.latest(db, user_id)
        if not events:
            return {
                "success": True,
                "updated": False,
                "message": "目前还没有足够的行为记录，暂时无法生成成长记忆。",
                "data": latest,
            }
        event_ids = [event.id for event in events]
        behavior_start = min(event.created_at for event in events)
        behavior_end = max(event.created_at for event in events)
        if latest and latest.behavior_end_time and behavior_end <= latest.behavior_end_time:
            return {
                "success": True,
                "updated": False,
                "message": "暂无新的行为记录，当前成长记忆无需更新。",
                "data": latest,
            }

        history = (
            db.query(GrowthMemorySnapshot)
            .filter(GrowthMemorySnapshot.user_id == user_id)
            .order_by(GrowthMemorySnapshot.created_at.desc())
            .limit(HISTORY_LIMIT)
            .all()
        )
        opportunity_map = self._load_related_opportunities(db, events)
        payload = self._build_model_input(user, events, opportunity_map, history)
        parsed = self._call_model(payload)
        snapshot = GrowthMemorySnapshot(
            user_id=user_id,
            current_stage=parsed["current_stage"],
            recent_focus=parsed["recent_focus"],
            preference_changes=parsed["preference_changes"],
            overall_observation=parsed["overall_observation"],
            next_stage_advice=parsed["next_stage_advice"],
            behavior_count=len(events),
            behavior_start_time=behavior_start,
            behavior_end_time=behavior_end,
            analyzed_event_ids=dumps(event_ids),
            referenced_memory_ids=dumps([item.id for item in history]),
            model_name=self._model_name(),
            prompt_version=PROMPT_VERSION,
        )
        try:
            db.add(snapshot)
            db.commit()
            db.refresh(snapshot)
        except SQLAlchemyError:
            db.rollback()
            logger.exception("save growth memory snapshot failed user_id=%s", user_id)
            raise RuntimeError("成长记忆保存失败")
        return {
            "success": True,
            "updated": True,
            "message": "成长记忆已更新。",
            "data": snapshot,
        }

    def _build_model_input(
        self,
        user: UserProfile,
        events: list[UserEvent],
        opportunity_map: dict[int, Opportunity],
        history: list[GrowthMemorySnapshot],
    ) -> dict[str, Any]:
        events_asc = sorted(events, key=lambda item: item.created_at)
        counts_by_type: Counter[str] = Counter(event.event_type for event in events)
        counts_by_category: Counter[str] = Counter()
        event_summaries = []
        opportunity_actions: dict[int, list[str]] = defaultdict(list)

        for event in events_asc:
            meta = loads_dict(event.event_metadata)
            opportunity_id = self._event_opportunity_id(event)
            opportunity = opportunity_map.get(opportunity_id) if opportunity_id else None
            category = opportunity.category if opportunity else str(meta.get("category") or meta.get("opportunity_category") or "未关联机会")
            counts_by_category[category] += 1
            if opportunity_id:
                opportunity_actions[opportunity_id].append(event.event_type)
            event_summaries.append(
                {
                    "id": event.id,
                    "event_type": event.event_type,
                    "weight_hint": self._weight_hint(event.event_type),
                    "created_at": event.created_at.isoformat(),
                    "opportunity_id": opportunity_id,
                    "opportunity_title": opportunity.name if opportunity else str(meta.get("opportunity_name") or meta.get("title") or ""),
                    "opportunity_type": category,
                }
            )

        representative = self._representative_events(event_summaries)
        related_opportunities = []
        for opportunity_id, actions in opportunity_actions.items():
            opportunity = opportunity_map.get(opportunity_id)
            if not opportunity:
                continue
            related_opportunities.append(
                {
                    "id": opportunity.id,
                    "title": opportunity.name,
                    "type": opportunity.category,
                    "keywords": self._keywords(opportunity),
                    "difficulty": self._difficulty(opportunity),
                    "deadline": opportunity.deadline,
                    "target_audience": opportunity.target_audience,
                    "summary": (opportunity.summary or "")[:220],
                    "verification_status": opportunity.verification_status,
                    "user_actions": list(dict.fromkeys(actions)),
                }
            )

        return {
            "user_profile": {
                "major": user.major_direction,
                "grade": user.grade_identity,
                "goals": loads_list(user.current_goals),
                "skills": loads_list(user.skills),
                "interests": loads_list(user.interested_fields),
                "dislikes": loads_list(user.disliked_contents),
                "preferences": {
                    "detailed_needs": user.detailed_needs,
                    "time_preference": user.time_preference,
                    "location_preference": user.location_preference,
                },
            },
            "behavior_summary": {
                "total_count": len(events),
                "time_range": {
                    "start": events_asc[0].created_at.isoformat() if events_asc else "",
                    "end": events_asc[-1].created_at.isoformat() if events_asc else "",
                },
                "counts_by_type": dict(counts_by_type),
                "counts_by_opportunity_type": dict(counts_by_category),
                "strong_action_signals": [item for item in representative if item["event_type"] in STRONG_ACTION_EVENTS],
                "positive_signals": [item for item in representative if item["event_type"] in POSITIVE_EVENTS],
                "negative_signals": [item for item in representative if item["event_type"] in NEGATIVE_EVENTS],
                "recent_representative_events": representative,
            },
            "related_opportunities": related_opportunities[:20],
            "recent_growth_memories": [
                {
                    "version_id": item.id,
                    "created_at": item.created_at.isoformat() if item.created_at else "",
                    "current_stage": item.current_stage,
                    "recent_focus": item.recent_focus,
                    "preference_changes": item.preference_changes,
                    "overall_observation": item.overall_observation,
                    "next_stage_advice": item.next_stage_advice,
                }
                for item in history
            ],
        }

    def _call_model(self, payload: dict[str, Any]) -> dict[str, str]:
        system = """
你是一名负责分析用户机会选择、学习发展和行动变化的成长顾问。

你的任务不是逐条复述用户行为，也不是根据单次点击定义用户的长期偏好。

你需要结合：
1. 用户画像
2. 最近用户行为
3. 行为对应的机会信息
4. 最近 3 版历史成长记忆

判断用户近期所处阶段、持续关注的重点、判断标准的变化，以及下一阶段更合适的发展方向。

分析时必须遵守以下规则：
1. 单次浏览属于弱信号，不能直接证明用户喜欢某个方向。
2. 收藏、标记有用和验证机会属于明确兴趣信号。
3. 创建待办、创建日历和开始申请属于强行动信号。历史沟通材料类行为只能作为旧行为参考。
4. 只有重复出现的行为，或兴趣信号与行动信号相互支持时，才可以形成较强判断。
5. 需要参考历史成长记忆，判断用户是否发生阶段变化，而不是每次从零分析。
6. 长期职业方向只能谨慎推断，不能武断下结论。
7. 不要使用空泛、鸡汤式语言。
8. 不要输出分析步骤、推理过程或内部思考。
9. 所有内容使用中文。
10. 全文使用第二人称“你”。

请生成以下五部分：
一、当前阶段
二、近期关注重点
三、判断标准与偏好变化
四、整体观察
五、下一阶段建议

请返回严格的 JSON，不要返回 Markdown，不要添加代码块，不要增加额外字段。
输出结构：
{
  "current_stage": "当前阶段内容",
  "recent_focus": "近期关注重点内容",
  "preference_changes": "判断标准与偏好变化内容",
  "overall_observation": "整体观察内容",
  "next_stage_advice": "下一阶段建议内容"
}
"""
        content = self.gateway.chat(
            messages=[
                {"role": "system", "content": system.strip()},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0.25,
            max_retries=1,
            response_format={"type": "json_object"},
        )
        parsed = self._parse_json_object(content)
        missing = [field for field in REQUIRED_FIELDS if not str(parsed.get(field) or "").strip()]
        if missing:
            raise RuntimeError("成长记忆生成结果缺少字段：" + "、".join(missing))
        return {field: str(parsed.get(field) or "").strip() for field in REQUIRED_FIELDS}

    def _load_related_opportunities(self, db: Session, events: list[UserEvent]) -> dict[int, Opportunity]:
        ids = {opportunity_id for event in events if (opportunity_id := self._event_opportunity_id(event))}
        if not ids:
            return {}
        return {item.id: item for item in db.query(Opportunity).filter(Opportunity.id.in_(ids)).all()}

    def _event_opportunity_id(self, event: UserEvent) -> int | None:
        meta = loads_dict(event.event_metadata)
        for key in ["opportunity_id", "target_opportunity_id"]:
            value = meta.get(key)
            if str(value).isdigit():
                return int(value)
        if event.event_target and event.event_target.isdigit() and event.event_type not in (POSITIVE_EVENTS | NEGATIVE_EVENTS):
            return int(event.event_target)
        return None

    def _representative_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        priority = {"strong_action": 4, "positive_interest": 3, "negative_or_filter": 2, "weak_interest": 1, "context": 0}
        sorted_events = sorted(events, key=lambda item: (priority.get(item["weight_hint"], 0), item["created_at"]), reverse=True)
        return sorted_events[:18]

    def _weight_hint(self, event_type: str) -> str:
        if event_type in STRONG_ACTION_EVENTS:
            return "strong_action"
        if event_type in POSITIVE_EVENTS:
            return "positive_interest"
        if event_type in NEGATIVE_EVENTS:
            return "negative_or_filter"
        if event_type in WEAK_EVENTS:
            return "weak_interest"
        return "context"

    def _keywords(self, opportunity: Opportunity) -> list[str]:
        text = " ".join([opportunity.name or "", opportunity.category or "", opportunity.summary or "", opportunity.requirements or ""])
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}|[\u4e00-\u9fff]{2,}", text)
        stopwords = {"机会", "招募", "通知", "报名", "项目", "相关", "官方", "活动", "申请"}
        result = []
        for token in tokens:
            if token not in stopwords and token not in result:
                result.append(token)
        return result[:8]

    def _difficulty(self, opportunity: Opportunity) -> str:
        cost = float(opportunity.cost or 0)
        if cost >= 0.7:
            return "偏高"
        if cost >= 0.4:
            return "中等"
        return "较低"

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        cleaned = (text or "").strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?", "", cleaned, flags=re.IGNORECASE).strip()
            cleaned = re.sub(r"```$", "", cleaned).strip()
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end >= start:
            cleaned = cleaned[start : end + 1]
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise RuntimeError("模型没有返回 JSON 对象")
        return parsed

    def _model_name(self) -> str:
        try:
            return str(self.gateway.debug_config().get("active_model") or "")
        except Exception:
            return ""

    def _lock_for_user(self, user_id: int) -> threading.Lock:
        with _LOCKS_GUARD:
            if user_id not in _USER_LOCKS:
                _USER_LOCKS[user_id] = threading.Lock()
            return _USER_LOCKS[user_id]
