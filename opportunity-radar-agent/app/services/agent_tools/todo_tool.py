from __future__ import annotations

from app.services.agent_tools.base import BaseTool, OpportunityToolContext, ToolOutput


class TodoOutput(ToolOutput):
    todo_items: list[str]
    suggested_deadline: str


class MockTodoTool(BaseTool):
    name = "create_todo"
    description = "为机会生成行动待办。"
    input_model = OpportunityToolContext
    output_model = TodoOutput

    def run(self, tool_input: OpportunityToolContext) -> TodoOutput:
        items = [
            "打开并保存官方/报名链接",
            "确认截止时间、地点和参与成本",
            "整理一版 1 页简历或项目经历简介",
        ]
        if tool_input.organizer:
            items.append(f"检索「{tool_input.organizer}」背景，确认主办方可信度")
        if tool_input.requirements:
            items.append("逐条对照申请要求，标记缺失材料")
        if tool_input.extra_params.get("include_email", True):
            items.append("必要时发送咨询邮件，确认申请细节")
        return TodoOutput(
            status="success",
            summary="已生成 mock 待办清单。",
            todo_items=items,
            suggested_deadline=tool_input.deadline or "unknown",
        )
