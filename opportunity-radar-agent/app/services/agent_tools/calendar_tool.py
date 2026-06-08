from __future__ import annotations

from datetime import datetime, timedelta

from app.services.agent_tools.base import BaseTool, OpportunityToolContext, ToolOutput


class CalendarReminderOutput(ToolOutput):
    mock_event_id: str
    reminder_time: str
    summary: str


class MockCalendarTool(BaseTool):
    name = "create_calendar_reminder"
    description = "为机会创建 mock 日历提醒。"
    input_model = OpportunityToolContext
    output_model = CalendarReminderOutput
    requires_confirmation = True

    def run(self, tool_input: OpportunityToolContext) -> CalendarReminderOutput:
        reminder_time = tool_input.extra_params.get("reminder_time")
        if not reminder_time:
            reminder_time = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d 09:00")
        summary = f"已模拟创建「{tool_input.title}」提醒。建议在截止前确认材料和报名入口。"
        return CalendarReminderOutput(
            status="success",
            mock_event_id=f"mock-cal-{tool_input.opportunity_id}-{int(datetime.utcnow().timestamp())}",
            reminder_time=reminder_time,
            summary=summary,
        )
