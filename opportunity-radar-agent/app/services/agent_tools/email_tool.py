from __future__ import annotations

from app.services.agent_tools.base import BaseTool, OpportunityToolContext, ToolOutput


class EmailDraftOutput(ToolOutput):
    subject: str
    body: str
    suggested_recipient: str


class MockEmailDraftTool(BaseTool):
    name = "draft_email"
    description = "生成联系导师、主办方或报名咨询邮件草稿，不发送。"
    input_model = OpportunityToolContext
    output_model = EmailDraftOutput

    def run(self, tool_input: OpportunityToolContext) -> EmailDraftOutput:
        profile = tool_input.user_profile
        student_name = profile.get("name", "同学")
        major = profile.get("major_direction", "相关专业")
        goals = "、".join(profile.get("current_goals", [])) or "个人发展"
        recipient = tool_input.extra_params.get("recipient") or tool_input.organizer or "主办方/老师"
        subject = f"关于「{tool_input.title}」的咨询与申请意向"
        body = (
            f"{recipient}您好：\n\n"
            f"我是{student_name}，方向是{major}，近期关注{goals}相关机会。"
            f"我在通知中看到「{tool_input.title}」，希望进一步了解申请要求、时间安排和后续参与方式。\n\n"
            "如果方便的话，想请问：\n"
            "1. 当前是否仍在开放报名/申请？\n"
            "2. 对申请者背景或每周投入时间有什么具体要求？\n"
            "3. 是否需要提前提交简历、成绩单或项目材料？\n\n"
            "感谢您的时间，期待回复。\n\n"
            f"{student_name}"
        )
        return EmailDraftOutput(
            status="success",
            summary="已生成邮件草稿，当前仅保存文本，不会自动发送。",
            subject=subject,
            body=body,
            suggested_recipient=recipient,
        )
