from sqlalchemy.orm import Session

from app.models.entities import ToolCall
from app.services.json_utils import dumps


SUPPORTED_TOOLS = {"search", "calendar", "email_draft", "todo"}


class ToolService:
    def call(self, db: Session, tool_name: str, user_id: int | None, opportunity_id: int | None, payload: dict) -> ToolCall:
        if tool_name not in SUPPORTED_TOOLS:
            raise ValueError(f"暂不支持工具：{tool_name}")
        result = {
            "message": f"{tool_name} 工具已 mock 调用",
            "payload_echo": payload,
        }
        if tool_name == "calendar":
            result["suggestion"] = "已模拟创建截止日期提醒"
        elif tool_name == "email_draft":
            result["suggestion"] = "已模拟生成邮件草稿"
        elif tool_name == "todo":
            result["suggestion"] = "已模拟添加待办事项"
        elif tool_name == "search":
            result["suggestion"] = "已模拟搜索相关背景信息"
        call = ToolCall(
            user_id=user_id,
            opportunity_id=opportunity_id,
            tool_name=tool_name,
            payload=dumps(payload),
            status="mocked",
            result=dumps(result),
        )
        db.add(call)
        db.commit()
        db.refresh(call)
        return call
