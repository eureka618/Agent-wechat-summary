from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import Opportunity, ToolCallLog, UserProfile
from app.services.agent_tools.base import OpportunityToolContext, ToolRegistry
from app.services.agent_tools.calendar_tool import CalendarTool
from app.services.agent_tools.enrichment_tool import EnrichOpportunityInput, EnrichOpportunityTool
from app.services.agent_tools.todo_tool import TodoTool
from app.services.agent_tools.verification_tool import VerifyOpportunityInput, VerifyOpportunityTool
from app.services.calendar_event_service import CalendarEventService
from app.services.growth_memory.memory_manager import GrowthMemoryManager
from app.services.json_utils import dumps, loads_list
from app.services.opportunity_state_service import OpportunityStateService


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("verify", VerifyOpportunityTool())
    registry.register("enrich", EnrichOpportunityTool())
    registry.register("calendar", CalendarTool())
    registry.register("todo", TodoTool())
    return registry


class OpportunityAgent:
    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or build_default_registry()

    def run_opportunity_action(
        self,
        db: Session,
        user_id: int,
        opportunity_id: int,
        action: str,
        extra_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        opportunity = db.get(Opportunity, opportunity_id)
        if not opportunity:
            raise ValueError("机会不存在")
        user = db.get(UserProfile, user_id)
        if not user:
            raise ValueError("用户画像不存在")

        extra_params = extra_params or {}
        tool = self.registry.get(action)
        tool_input = self._build_tool_input(tool.name, user, opportunity, extra_params)
        output = tool.run(tool_input)
        output_payload = output.model_dump(mode="json")

        self._apply_tool_result(opportunity, action, output_payload)
        calendar_event_created = True
        if action == "calendar":
            calendar_event = CalendarEventService().upsert_from_opportunity(
                db=db,
                user_id=user_id,
                opportunity=opportunity,
                reminder_time=str(output_payload.get("reminder_time") or ""),
            )
            if calendar_event:
                output_payload.update(
                    {
                        "status": "success",
                        "calendar_created": True,
                        "event_id": str(calendar_event.id),
                        "reminder_time": calendar_event.start_time.isoformat(sep=" ") if calendar_event.start_time else "",
                        "summary": f"已加入日历提醒：{calendar_event.title}",
                    }
                )
            else:
                calendar_event_created = False
                extraction = CalendarEventService().extract_date(opportunity)
                output_payload.update(
                    {
                        "status": "calendar_skipped_no_date",
                        "calendar_created": False,
                        "event_id": "",
                        "reminder_time": "",
                        "date_status": extraction.date_status,
                        "date_reason": extraction.reason,
                        "summary": "已生成待办；原文缺少明确日期，暂未加入日历提醒。",
                    }
                )
        if action == "calendar" and not calendar_event_created:
            state = OpportunityStateService().get_state(db, user_id, opportunity_id)
        else:
            state = OpportunityStateService().advance_for_action(db, user_id, opportunity_id, action)
        log = ToolCallLog(
            user_id=user_id,
            opportunity_id=opportunity_id,
            action=action,
            tool_name=tool.name,
            input_json=dumps(tool_input.model_dump(mode="json")),
            output_json=dumps(output_payload),
            status=output_payload.get("status", "success"),
        )
        db.add(log)
        db.commit()
        db.refresh(log)
        db.refresh(opportunity)

        event_type = {
            "verify": "verify_opportunity",
            "calendar": "create_calendar" if calendar_event_created else "",
            "todo": "create_todo",
        }.get(action)
        if event_type:
            GrowthMemoryManager().log_event(
                db,
                user_id=user_id,
                event_type=event_type,
                event_target=str(opportunity_id),
                event_metadata=self._event_metadata(opportunity, output_payload),
            )

        return {
            "action": action,
            "status": output_payload.get("status", "success"),
            "tool_name": tool.name,
            "requires_confirmation": tool.requires_confirmation,
            "result": output_payload,
            "log_id": log.id,
            "opportunity_state": state.state if state else "",
        }

    def _build_tool_input(
        self,
        tool_name: str,
        user: UserProfile,
        opportunity: Opportunity,
        extra_params: dict[str, Any],
    ) -> OpportunityToolContext:
        profile = {
            "name": user.name,
            "major_direction": user.major_direction,
            "grade_identity": user.grade_identity,
            "current_goals": loads_list(user.current_goals),
            "skills": loads_list(user.skills),
            "interested_fields": loads_list(user.interested_fields),
            "detailed_needs": user.detailed_needs,
            "time_preference": user.time_preference,
            "location_preference": user.location_preference,
        }
        payload = {
            "opportunity_id": opportunity.id,
            "user_id": user.id,
            "title": opportunity.name,
            "organizer": opportunity.organizer,
            "link": opportunity.link,
            "description": opportunity.summary,
            "deadline": opportunity.deadline,
            "location": opportunity.location,
            "requirements": opportunity.requirements,
            "target_audience": opportunity.target_audience,
            "user_profile": profile,
            "extra_params": extra_params,
        }
        if tool_name == "verify_opportunity":
            return VerifyOpportunityInput(**payload)
        if tool_name == "enrich_opportunity":
            missing_fields = [
                field
                for field, value in {
                    "official_url": opportunity.official_url or opportunity.link,
                    "registration_url": opportunity.registration_url or opportunity.link,
                    "deadline": opportunity.deadline,
                    "location": opportunity.location,
                    "requirements": opportunity.requirements,
                    "target_audience": opportunity.target_audience,
                }.items()
                if not value
            ]
            return EnrichOpportunityInput(**payload, current_missing_fields=missing_fields)
        return OpportunityToolContext(**payload)

    def _apply_tool_result(self, opportunity: Opportunity, action: str, output: dict[str, Any]) -> None:
        now = datetime.utcnow()
        if action == "verify":
            opportunity.verification_status = output.get("verification_status", "")
            opportunity.credibility_score = output.get("credibility_score")
            opportunity.risk_level = output.get("risk_level", "")
            opportunity.risk_flags = dumps(output.get("risk_flags", []))
            opportunity.verification_summary = output.get("verification_summary", "")
            opportunity.evidence_sources = dumps(output.get("evidence_sources", []))
            opportunity.verified_at = now
        elif action == "enrich":
            opportunity.official_url = self._known(output.get("official_url"), opportunity.official_url)
            opportunity.registration_url = self._known(output.get("registration_url"), opportunity.registration_url)
            opportunity.deadline = self._known(output.get("deadline"), opportunity.deadline)
            opportunity.location = self._known(output.get("location"), opportunity.location)
            opportunity.requirements = self._known(output.get("requirements"), opportunity.requirements)
            opportunity.target_audience = self._known(output.get("target_audience"), opportunity.target_audience)
            opportunity.enriched_at = now

    def _known(self, value: Any, fallback: str) -> str:
        if value and value != "unknown":
            return str(value)
        return fallback

    def _event_metadata(self, opportunity: Opportunity, output: dict[str, Any]) -> dict[str, Any]:
        return {
            "opportunity_id": opportunity.id,
            "title": opportunity.name,
            "category": opportunity.category,
            "organizer": opportunity.organizer,
            "deadline": opportunity.deadline,
            "verification_status": output.get("verification_status"),
            "risk_level": output.get("risk_level"),
        }
