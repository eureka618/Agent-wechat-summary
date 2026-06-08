from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    opportunity_id: int


class ToolOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str = "success"
    summary: str = ""


class BaseTool(ABC):
    name: str
    description: str
    input_model: type[ToolInput]
    output_model: type[ToolOutput]
    requires_confirmation: bool = False

    @abstractmethod
    def run(self, tool_input: ToolInput) -> ToolOutput:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, action: str, tool: BaseTool) -> None:
        self._tools[action] = tool

    def get(self, action: str) -> BaseTool:
        if action not in self._tools:
            raise KeyError(f"Unsupported opportunity action: {action}")
        return self._tools[action]

    def list_actions(self) -> list[str]:
        return sorted(self._tools)


class OpportunityToolContext(ToolInput):
    user_id: int
    title: str
    organizer: str = ""
    link: str = ""
    description: str = ""
    deadline: str = ""
    location: str = ""
    requirements: str = ""
    target_audience: str = ""
    user_profile: dict[str, Any] = Field(default_factory=dict)
    extra_params: dict[str, Any] = Field(default_factory=dict)
