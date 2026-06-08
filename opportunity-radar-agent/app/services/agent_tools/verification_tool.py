from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.services.agent_tools.base import BaseTool, OpportunityToolContext, ToolOutput
from app.services.agent_tools.search_tool import MockSearchProvider, SearchProvider, SearchResult


class VerifyOpportunityInput(OpportunityToolContext):
    pass


class VerifyOpportunityOutput(ToolOutput):
    verification_status: Literal["verified", "uncertain", "suspicious", "failed"]
    credibility_score: int = Field(ge=0, le=100)
    risk_level: Literal["low", "medium", "high"]
    risk_flags: list[str] = Field(default_factory=list)
    verification_summary: str
    evidence_sources: list[SearchResult] = Field(default_factory=list)


class VerifyOpportunityTool(BaseTool):
    name = "verify_opportunity"
    description = "按需核验单条机会真实性，MVP 使用 mock 搜索和规则判断。"
    input_model = VerifyOpportunityInput
    output_model = VerifyOpportunityOutput

    def __init__(self, search_provider: SearchProvider | None = None) -> None:
        self.search_provider = search_provider or MockSearchProvider()

    def run(self, tool_input: VerifyOpportunityInput) -> VerifyOpportunityOutput:
        text = " ".join(
            [
                tool_input.title,
                tool_input.organizer,
                tool_input.link,
                tool_input.description,
                tool_input.requirements,
            ]
        )
        evidence = self.search_provider.search(text, max_results=5)
        risk_flags = self._risk_flags(text)
        score = 55
        if tool_input.link:
            score += 12
        if any(word in tool_input.organizer for word in ["大学", "学院", "实验室", "官方", "招生办", "就业中心", "学会"]):
            score += 20
        if any(item.source_type == "official_like" for item in evidence):
            score += 10
        if not tool_input.link:
            risk_flags.append("缺少可核验链接")
            score -= 8
        if len(tool_input.title.strip()) < 8:
            risk_flags.append("标题过短或信息不足")
            score -= 10
        if risk_flags:
            score -= min(30, len(risk_flags) * 10)
        score = max(0, min(100, score))

        if score >= 78 and not risk_flags:
            status = "verified"
            risk_level = "low"
            summary = "模拟核验显示该机会具备较完整的主办方和来源特征，暂未发现明显风险词。"
        elif score < 45 or any("高风险" in flag or "承诺" in flag for flag in risk_flags):
            status = "suspicious"
            risk_level = "high"
            summary = "模拟核验发现较多风险信号，建议人工确认官方来源后再行动。"
        else:
            status = "uncertain"
            risk_level = "medium" if risk_flags else "low"
            summary = "模拟核验结果信息不足，无法确认真实性；建议补充官方链接或主办方页面。"

        return VerifyOpportunityOutput(
            status="success",
            summary=summary,
            verification_status=status,
            credibility_score=score,
            risk_level=risk_level,
            risk_flags=risk_flags,
            verification_summary=summary,
            evidence_sources=evidence,
        )

    def _risk_flags(self, text: str) -> list[str]:
        mapping = {
            "限时付费": "出现限时付费表达",
            "保录取": "出现保录取等承诺型表达",
            "内部名额": "出现内部名额等高风险表达",
            "包过": "出现包过等不可信承诺",
            "高价": "费用表达可能偏高",
            "稳赚": "出现收益承诺型表达",
        }
        return [flag for keyword, flag in mapping.items() if keyword in text]
