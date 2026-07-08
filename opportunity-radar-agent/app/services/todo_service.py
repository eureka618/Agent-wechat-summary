from __future__ import annotations

from datetime import datetime
import logging
import re

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.entities import Opportunity, Todo, UserEvent, UserProfile
from app.services.json_utils import dumps
from app.services.opportunity_state_service import OpportunityStateService


logger = logging.getLogger(__name__)

UNKNOWN_DEADLINE_WORDS = {"", "未知", "未注明", "不详", "无", "none", "null", "unknown"}


class TodoService:
    def create_from_opportunity(self, db: Session, user_id: int, opportunity_id: int) -> Todo:
        try:
            user = db.get(UserProfile, user_id)
            if not user:
                raise ValueError("用户画像不存在")
            opportunity = db.get(Opportunity, opportunity_id)
            if not opportunity:
                raise ValueError("机会不存在")

            existing = (
                db.query(Todo)
                .filter(Todo.user_id == user_id, Todo.opportunity_id == opportunity_id)
                .first()
            )
            if existing:
                setattr(existing, "already_exists", True)
                logger.info("todo already exists user_id=%s opportunity_id=%s todo_id=%s", user_id, opportunity_id, existing.id)
                return existing

            deadline, deadline_note = self._deadline_fields(opportunity.deadline)
            todo = Todo(
                user_id=user_id,
                opportunity_id=opportunity_id,
                title=f"准备并申请：{opportunity.name}",
                description=self._description(opportunity, deadline_note),
                deadline=deadline,
                deadline_note=deadline_note,
                status="pending",
                priority=self._priority(opportunity, deadline),
            )
            db.add(todo)
            db.flush()

            OpportunityStateService().advance_state(db, user_id, opportunity_id, "todo_created")
            self._log_event(db, user_id, opportunity)
            db.commit()
            db.refresh(todo)
            setattr(todo, "already_exists", False)
            logger.info("todo created user_id=%s opportunity_id=%s todo_id=%s", user_id, opportunity_id, todo.id)
            return todo
        except ValueError:
            db.rollback()
            raise
        except SQLAlchemyError:
            db.rollback()
            logger.exception("create todo db failure user_id=%s opportunity_id=%s", user_id, opportunity_id)
            raise RuntimeError("待办创建失败")
        except Exception:
            db.rollback()
            logger.exception("create todo failed user_id=%s opportunity_id=%s", user_id, opportunity_id)
            raise

    def _deadline_fields(self, raw_deadline: str) -> tuple[str | None, str]:
        text = (raw_deadline or "").strip()
        if text.lower() in UNKNOWN_DEADLINE_WORDS:
            return None, "原文未注明截止时间，请先确认"
        parsed = self._parse_date(text)
        if parsed:
            return parsed.strftime("%Y-%m-%d"), ""
        return None, "原文未注明截止时间，请先确认"

    def _parse_date(self, text: str) -> datetime | None:
        patterns = [
            r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            year, month, day = match.groups()
            try:
                return datetime(int(year), int(month), int(day))
            except ValueError:
                return None
        return None

    def _description(self, opportunity: Opportunity, deadline_note: str) -> str:
        links = [
            item
            for item in [opportunity.registration_url, opportunity.official_url, opportunity.link]
            if item
        ]
        parts = [
            opportunity.summary or "请先阅读原文，确认申请要求和材料清单。",
            f"行动建议：{self._action_suggestion(opportunity)}",
        ]
        if links:
            parts.append("相关链接：" + " / ".join(links))
        if deadline_note:
            parts.append(f"截止时间提示：{deadline_note}")
        parts.append(f"来源 opportunity_id：{opportunity.id}")
        return "\n".join(parts)

    def _action_suggestion(self, opportunity: Opportunity) -> str:
        if opportunity.category in {"实习", "科研", "实验室招募"}:
            return "确认报名入口，整理简历/项目经历，并核对申请要求。"
        if opportunity.category in {"竞赛", "夏令营"}:
            return "确认截止时间、报名材料和参与条件，必要时寻找队友或导师建议。"
        return "确认入口、截止时间和参与条件，再决定是否申请。"

    def _priority(self, opportunity: Opportunity, deadline: str | None) -> str:
        if deadline:
            try:
                days = (datetime.strptime(deadline, "%Y-%m-%d").date() - datetime.utcnow().date()).days
                if days <= 3:
                    return "high"
                if days <= 10:
                    return "medium"
            except ValueError:
                pass
        if opportunity.category in {"实习", "夏令营", "实验室招募"}:
            return "medium"
        return "low"

    def _log_event(self, db: Session, user_id: int, opportunity: Opportunity) -> None:
        db.add(
            UserEvent(
                user_id=user_id,
                event_type="create_todo",
                event_target=str(opportunity.id),
                event_metadata=dumps(
                    {
                        "source": "recommendation_card",
                        "opportunity_id": opportunity.id,
                        "opportunity_category": opportunity.category,
                        "category": opportunity.category,
                        "opportunity_name": opportunity.name,
                    }
                ),
            )
        )
