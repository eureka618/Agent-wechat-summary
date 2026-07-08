from __future__ import annotations

from pydantic import Field

from app.services.agent_tools.base import BaseTool, OpportunityToolContext, ToolOutput
from app.services.agent_tools.search_tool import LocalSearchProvider, SearchProvider


class EnrichOpportunityInput(OpportunityToolContext):
    current_missing_fields: list[str] = Field(default_factory=list)


class EnrichOpportunityOutput(ToolOutput):
    official_url: str = "unknown"
    registration_url: str = "unknown"
    deadline: str = "unknown"
    location: str = "unknown"
    requirements: str = "unknown"
    fee: str = "unknown"
    target_audience: str = "unknown"
    unknown_fields: list[str] = Field(default_factory=list)
    enrichment_summary: str


class EnrichOpportunityTool(BaseTool):
    name = "enrich_opportunity"
    description = "按需补全单条机会信息，基于已有字段和本地规则检索。"
    input_model = EnrichOpportunityInput
    output_model = EnrichOpportunityOutput

    def __init__(self, search_provider: SearchProvider | None = None) -> None:
        self.search_provider = search_provider or LocalSearchProvider()

    def run(self, tool_input: EnrichOpportunityInput) -> EnrichOpportunityOutput:
        self.search_provider.search(f"{tool_input.title} {tool_input.organizer}", max_results=3)
        official_url = tool_input.link or "unknown"
        registration_url = tool_input.link or "unknown"
        deadline = tool_input.deadline or "unknown"
        location = tool_input.location or "unknown"
        requirements = tool_input.requirements or "unknown"
        target_audience = tool_input.target_audience or "unknown"
        unknown_fields = [
            name
            for name, value in {
                "official_url": official_url,
                "registration_url": registration_url,
                "deadline": deadline,
                "location": location,
                "requirements": requirements,
                "fee": "unknown",
                "target_audience": target_audience,
            }.items()
            if value == "unknown"
        ]
        summary = "已基于现有文章字段完成局部补全；无法确认的信息保持 unknown，等待真实搜索 API 接入。"
        return EnrichOpportunityOutput(
            status="success",
            summary=summary,
            official_url=official_url,
            registration_url=registration_url,
            deadline=deadline,
            location=location,
            requirements=requirements,
            fee="unknown",
            target_audience=target_audience,
            unknown_fields=unknown_fields,
            enrichment_summary=summary,
        )
