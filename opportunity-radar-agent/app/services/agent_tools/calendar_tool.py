from __future__ import annotations

from datetime import datetime

from app.services.agent_tools.base import BaseTool, OpportunityToolContext, ToolOutput


class CalendarReminderOutput(ToolOutput):
    event_id: str = ""
    reminder_time: str = ""
    summary: str


class CalendarTool(BaseTool):
    name = "create_calendar_reminder"
    description = "为机会创建本地日历提醒计划。"
    input_model = OpportunityToolContext
    output_model = CalendarReminderOutput
    requires_confirmation = True

    def run(self, tool_input: OpportunityToolContext) -> CalendarReminderOutput:
        reminder_time = tool_input.extra_params.get("reminder_time")
        if not reminder_time:
            reminder_time = tool_input.deadline or ""
        summary = f"正在根据「{tool_input.title}」的明确日期创建提醒；若原文缺少明确日期，将仅保留待办。"
        return CalendarReminderOutput(
            status="success",
            event_id=f"cal-{tool_input.opportunity_id}-{int(datetime.utcnow().timestamp())}",
            reminder_time=reminder_time,
            summary=summary,
        )
