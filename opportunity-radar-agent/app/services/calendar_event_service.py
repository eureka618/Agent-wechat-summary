from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
import re

from sqlalchemy.orm import Session

from app.models.entities import CalendarEvent, Opportunity, Todo
from app.services.json_utils import loads_dict


UNKNOWN_DATE_WORDS = {"", "未知", "未注明", "不详", "无", "none", "null", "unknown", "待定", "暂无"}
AMBIGUOUS_DATE_WORDS = ["近期", "尽快", "报名中", "长期", "中旬", "下旬", "上旬", "月末", "月底", "月初", "另行通知"]


@dataclass
class DateExtraction:
    date_status: str = "unknown"
    date_type: str = "unknown"
    start_time: datetime | None = None
    end_time: datetime | None = None
    display_date_text: str = ""
    confidence: str = "low"
    reason: str = "原文未注明明确日期"


class CalendarEventService:
    def upsert_from_opportunity(
        self,
        db: Session,
        user_id: int,
        opportunity: Opportunity,
        reminder_time: str = "",
        source: str = "recommendation_card",
    ) -> CalendarEvent | None:
        extraction = self.extract_date(opportunity, override_text=reminder_time)
        if extraction.date_status != "known" or not extraction.start_time:
            return None
        event = (
            db.query(CalendarEvent)
            .filter(CalendarEvent.user_id == user_id, CalendarEvent.opportunity_id == opportunity.id)
            .first()
        )
        if not event:
            event = CalendarEvent(user_id=user_id, opportunity_id=opportunity.id)
            db.add(event)
        event.title = f"{self.reminder_type_label(extraction.date_type)}｜{self.short_title(opportunity.name)}"
        event.start_time = extraction.start_time
        event.end_time = extraction.end_time or extraction.start_time + timedelta(minutes=30)
        event.status = "scheduled"
        event.source = source
        event.updated_at = datetime.utcnow()
        db.flush()
        return event

    def list_for_user(self, db: Session, user_id: int) -> list[CalendarEvent]:
        events = (
            db.query(CalendarEvent)
            .filter(CalendarEvent.user_id == user_id, CalendarEvent.start_time.isnot(None))
            .order_by(CalendarEvent.start_time.asc())
            .all()
        )
        if not events:
            return []
        opportunities = {
            item.id: item
            for item in db.query(Opportunity)
            .filter(Opportunity.id.in_({event.opportunity_id for event in events}))
            .all()
        }
        formal_events: list[CalendarEvent] = []
        for event in events:
            opportunity = opportunities.get(event.opportunity_id)
            if not opportunity:
                continue
            extraction = self.extract_date(opportunity)
            if (
                extraction.date_status == "known"
                and extraction.start_time
                and event.start_time
                and event.start_time.date() == extraction.start_time.date()
            ):
                formal_events.append(event)
        return formal_events

    def list_uncertain_deadline_todos(self, db: Session, user_id: int) -> list[dict[str, Any]]:
        todos = (
            db.query(Todo)
            .filter(Todo.user_id == user_id)
            .order_by(Todo.created_at.desc())
            .all()
        )
        if not todos:
            return []
        opportunities = {
            item.id: item
            for item in db.query(Opportunity)
            .filter(Opportunity.id.in_({todo.opportunity_id for todo in todos}))
            .all()
        }
        formal_opportunity_ids = {
            event.opportunity_id
            for event in self.list_for_user(db, user_id)
        }
        items: list[dict[str, Any]] = []
        for todo in todos:
            if todo.opportunity_id in formal_opportunity_ids:
                continue
            opportunity = opportunities.get(todo.opportunity_id)
            if not opportunity:
                continue
            extraction = self.extract_date(opportunity)
            if extraction.date_status == "known" and extraction.start_time:
                continue
            items.append(
                {
                    "id": todo.id,
                    "user_id": todo.user_id,
                    "opportunity_id": todo.opportunity_id,
                    "title": todo.title,
                    "description": todo.description,
                    "deadline": None,
                    "deadline_note": todo.deadline_note or extraction.reason,
                    "status": todo.status,
                    "priority": todo.priority,
                    "created_at": todo.created_at,
                    "updated_at": todo.updated_at,
                    "already_exists": getattr(todo, "already_exists", False),
                    "short_title": self.short_title(opportunity.name),
                    "date_uncertain_reason": extraction.reason,
                }
            )
        return items

    def extract_date(self, opportunity: Opportunity, override_text: str = "") -> DateExtraction:
        candidates: list[tuple[str, str, str]] = []
        if override_text:
            candidates.append(("override", "deadline", override_text))
        raw_payload = loads_dict(opportunity.raw_payload)
        candidates.extend(
            [
                ("deadline", "deadline", opportunity.deadline or ""),
                ("registration_deadline", "registration_time", str(raw_payload.get("registration_deadline") or "")),
                ("event_time", "event_time", opportunity.event_time or ""),
                ("event_date", "event_time", str(raw_payload.get("event_date") or raw_payload.get("activity_time") or "")),
            ]
        )
        candidates.extend(
            [
                ("summary", "unknown", opportunity.summary or ""),
                ("requirements", "unknown", opportunity.requirements or ""),
                ("target_audience", "unknown", opportunity.target_audience or ""),
            ]
        )
        ambiguous: DateExtraction | None = None
        for field_name, date_type, text in candidates:
            result = self._extract_from_text(text, date_type)
            if result.date_status == "known":
                return result
            if result.date_status == "ambiguous" and not ambiguous:
                ambiguous = result
        return ambiguous or DateExtraction()

    def reminder_type_label(self, date_type: str) -> str:
        return {
            "deadline": "截止提醒",
            "event_time": "活动时间",
            "registration_time": "报名提醒",
            "date_range": "时间范围",
        }.get(date_type or "", "时间提醒")

    def short_title(self, title: str, limit: int = 28) -> str:
        clean = re.sub(r"\s+", " ", title or "未命名机会").strip()
        return clean if len(clean) <= limit else clean[:limit - 1] + "…"

    def _extract_from_text(self, value: str, date_type: str) -> DateExtraction:
        text = (value or "").strip()
        if not text or text.lower() in UNKNOWN_DATE_WORDS:
            return DateExtraction()
        normalized = text.replace("T", " ").replace("/", "-")
        matches = list(
            re.finditer(
                r"(20\d{2})[-.年](\d{1,2})[-.月](\d{1,2})日?(?:\s*(\d{1,2})[:：](\d{1,2}))?",
                normalized,
            )
        )
        if matches:
            start = self._datetime_from_match(matches[0])
            if not start:
                return DateExtraction(reason="日期格式无法解析")
            end_time = self._datetime_from_match(matches[1]) if len(matches) > 1 else None
            resolved_type = "date_range" if end_time else (date_type if date_type != "unknown" else self._infer_type(text))
            return DateExtraction(
                date_status="known",
                date_type=resolved_type,
                start_time=start,
                end_time=end_time,
                display_date_text=text[:120],
                confidence="high",
                reason="识别到包含年份的明确日期",
            )
        if any(word in text for word in AMBIGUOUS_DATE_WORDS):
            return DateExtraction(date_status="ambiguous", display_date_text=text[:120], reason=f"仅出现模糊时间：{text[:40]}")
        if re.search(r"\d{1,2}\s*月\s*(\d{1,2}\s*日?)?", text) or re.search(r"\d{1,2}[-.]\d{1,2}", text):
            return DateExtraction(date_status="ambiguous", display_date_text=text[:120], reason="缺少年份或具体日期")
        return DateExtraction()

    def _datetime_from_match(self, match: re.Match[str]) -> datetime | None:
        year, month, day, hour, minute = match.groups()
        try:
            return datetime(int(year), int(month), int(day), int(hour or 9), int(minute or 0))
        except ValueError:
            return None

    def _infer_type(self, text: str) -> str:
        if "报名" in text:
            return "registration_time"
        if "截止" in text or "ddl" in text.lower():
            return "deadline"
        return "event_time"

    def _parse_datetime(self, value: str) -> datetime | None:
        text = (value or "").strip()
        if not text:
            return None
        normalized = text.replace("T", " ").replace("/", "-")
        patterns = [
            r"(20\d{2})-(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{1,2})",
            r"(20\d{2})-(\d{1,2})-(\d{1,2})",
        ]
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if not match:
                continue
            groups = [int(item) for item in match.groups()]
            try:
                if len(groups) == 5:
                    return datetime(groups[0], groups[1], groups[2], groups[3], groups[4])
                return datetime(groups[0], groups[1], groups[2], 9, 0)
            except ValueError:
                return None
        return None
