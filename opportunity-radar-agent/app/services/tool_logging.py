from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import ToolCall, ToolCallLog
from app.services.json_utils import dumps


def record_tool_call(
    db: Session,
    *,
    user_id: int,
    opportunity_id: int,
    tool_name: str,
    query: str,
    provider: str,
    status: str,
    result_summary: str = "",
    error_message: str = "",
    payload: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
) -> ToolCallLog:
    call = ToolCall(
        user_id=user_id,
        opportunity_id=opportunity_id,
        tool_name=tool_name,
        payload=dumps(payload or {}),
        query=query,
        provider=provider,
        status=status,
        result_summary=result_summary,
        error_message=error_message,
        result=dumps(result or {}),
    )
    db.add(call)
    db.flush()
    log = ToolCallLog(
        user_id=user_id,
        opportunity_id=opportunity_id,
        action=tool_name,
        tool_name=tool_name,
        query=query,
        provider=provider,
        input_json=dumps(payload or {}),
        output_json=dumps(result or {}),
        status=status,
        result_summary=result_summary,
        error_message=error_message,
    )
    db.add(log)
    db.flush()
    return log
