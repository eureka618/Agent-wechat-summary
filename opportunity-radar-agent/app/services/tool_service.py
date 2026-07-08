from sqlalchemy.orm import Session

from app.models.entities import ToolCall
from app.services.json_utils import dumps


SUPPORTED_TOOLS = {"search", "calendar", "todo"}


class ToolService:
    def call(self, db: Session, tool_name: str, user_id: int | None, opportunity_id: int | None, payload: dict) -> ToolCall:
        if tool_name not in SUPPORTED_TOOLS:
            raise ValueError(f"暂不支持工具：{tool_name}")
        result = {
            "message": f"{tool_name} 工具已执行",
            "payload_echo": payload,
        }
        if tool_name == "calendar":
            result["suggestion"] = "已生成截止日期提醒计划"
        elif tool_name == "todo":
            result["suggestion"] = "已生成待办事项"
        elif tool_name == "search":
            result["suggestion"] = "已生成背景检索建议"
        call = ToolCall(
            user_id=user_id,
            opportunity_id=opportunity_id,
            tool_name=tool_name,
            payload=dumps(payload),
            status="success",
            result=dumps(result),
        )
        db.add(call)
        db.commit()
        db.refresh(call)
        return call
