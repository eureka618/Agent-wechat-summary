from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.entities import MemoryReflection, UserEvent
from app.services.json_utils import loads_dict


POSITIVE_EVENTS = {"save_opportunity", "verify_opportunity", "draft_email", "create_todo", "create_calendar", "mark_useful", "apply_opportunity"}
NEGATIVE_EVENTS = {"mark_irrelevant", "mark_not_interested", "mark_too_easy", "mark_too_hard"}


class ReflectionGenerator:
    def generate_reflection(self, db: Session, user_id: int) -> list[MemoryReflection]:
        since = datetime.utcnow() - timedelta(days=30)
        events = (
            db.query(UserEvent)
            .filter(UserEvent.user_id == user_id, UserEvent.created_at >= since)
            .order_by(UserEvent.created_at.desc())
            .limit(100)
            .all()
        )
        insights = self._build_insights(events)
        existing = {
            item.reflection_type: item
            for item in db.query(MemoryReflection).filter(MemoryReflection.user_id == user_id).all()
        }
        reflections: list[MemoryReflection] = []
        now = datetime.utcnow()
        for reflection_type, summary, confidence in insights:
            item = existing.get(reflection_type)
            if item:
                item.summary = summary
                item.confidence = confidence
                item.updated_at = now
            else:
                item = MemoryReflection(
                    user_id=user_id,
                    reflection_type=reflection_type,
                    summary=summary,
                    confidence=confidence,
                )
                db.add(item)
            reflections.append(item)
        db.commit()
        for item in reflections:
            db.refresh(item)
        return reflections

    def _build_insights(self, events: list[UserEvent]) -> list[tuple[str, str, float]]:
        if not events:
            return [("development_direction", "暂无足够近期行为形成稳定洞察。", 0.2)]

        category_counts: Counter[str] = Counter()
        positive_categories: Counter[str] = Counter()
        negative_categories: Counter[str] = Counter()
        action_counts: Counter[str] = Counter(event.event_type for event in events)
        keyword_counts: Counter[str] = Counter()
        category_actions: dict[str, Counter[str]] = defaultdict(Counter)

        for event in events:
            meta = loads_dict(event.event_metadata)
            category = str(meta.get("category") or "其他")
            title = str(meta.get("title") or event.event_target or "")
            category_counts[category] += 1
            category_actions[category][event.event_type] += 1
            if event.event_type in POSITIVE_EVENTS:
                positive_categories[category] += 1
            if event.event_type in NEGATIVE_EVENTS:
                negative_categories[category] += 1
            for token in self._keywords(title + " " + str(meta.get("organizer") or "")):
                keyword_counts[token] += 1

        top_categories = [name for name, _ in category_counts.most_common(3) if name != "其他"]
        positive = [name for name, _ in positive_categories.most_common(3) if positive_categories[name] > 0]
        negative = [name for name, _ in negative_categories.most_common(2) if negative_categories[name] > 0]
        top_keywords = [name for name, _ in keyword_counts.most_common(5)]

        lines = []
        if top_categories:
            lines.append(f"近期行为主要集中在{'、'.join(top_categories)}相关机会。")
        if positive:
            lines.append(f"对{'、'.join(positive)}类机会有更积极的后续动作。")
        if negative:
            lines.append(f"对{'、'.join(negative)}类机会出现较多忽略或负反馈。")
        if top_keywords:
            lines.append(f"近期反复出现的关注关键词包括：{'、'.join(top_keywords)}。")
        if action_counts["draft_email"] or action_counts["create_todo"] or action_counts["create_calendar"]:
            lines.append("用户不只是浏览机会，也开始进行申请准备、提醒和待办规划。")
        if not lines:
            lines.append("近期行为较分散，暂未形成稳定方向变化。")

        confidence = min(0.9, 0.3 + len(events) / 120)
        return [("development_direction", "\n".join(lines[:8]), round(confidence, 2))]

    def _keywords(self, text: str) -> list[str]:
        import re

        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]{1,}|[\u4e00-\u9fff]{2,}", text)
        stopwords = {"机会", "招募", "通知", "报名", "项目", "相关", "官方", "活动"}
        result = []
        for token in tokens:
            if token not in stopwords and token not in result:
                result.append(token)
        return result[:8]
