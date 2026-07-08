from __future__ import annotations

from pydantic import BaseModel

from app.services.agent_tools.base import BaseTool, OpportunityToolContext, ToolOutput


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    source_type: str = "local"


class SearchProvider:
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        raise NotImplementedError


class LocalSearchProvider(SearchProvider):
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        official_words = ["大学", "学院", "实验室", "官方", "招生办", "就业中心", "学会"]
        risk_words = ["限时付费", "保录取", "内部名额", "包过", "高价", "稳赚"]
        results: list[SearchResult] = []
        if any(word in query for word in official_words):
            results.append(
                SearchResult(
                    title=f"{query[:40]} 官方通知",
                    url="https://example.edu.cn/official-notice",
                    snippet="本地规则结果：标题、主办方和来源具有高校/学院/实验室等官方特征。",
                    source_type="official_like",
                )
            )
        if any(word in query for word in risk_words):
            results.append(
                SearchResult(
                    title="风险提示：疑似营销或承诺型表述",
                    url="unknown",
                    snippet="本地规则结果：出现保录取、内部名额、限时付费等高风险表达，需要人工复核。",
                    source_type="risk_flag",
                )
            )
        if not results:
            results.append(
                SearchResult(
                    title=f"{query[:40]} 相关结果",
                    url="unknown",
                    snippet="本地规则结果：未找到明确官方来源，建议补充主办方或官方链接后再判断。",
                    source_type="uncertain",
                )
            )
        return results[:max_results]


class TavilySearchProvider(SearchProvider):
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        raise NotImplementedError("TODO: 接入 Tavily Search API")


class SerpAPISearchProvider(SearchProvider):
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        raise NotImplementedError("TODO: 接入 SerpAPI")


class BingSearchProvider(SearchProvider):
    def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        raise NotImplementedError("TODO: 接入 Bing Search API")


class SearchToolOutput(ToolOutput):
    query: str
    results: list[SearchResult]


class LocalSearchTool(BaseTool):
    name = "local_search"
    description = "围绕单条 opportunity 做本地规则检索。"
    input_model = OpportunityToolContext
    output_model = SearchToolOutput

    def __init__(self, provider: SearchProvider | None = None) -> None:
        self.provider = provider or LocalSearchProvider()

    def run(self, tool_input: OpportunityToolContext) -> SearchToolOutput:
        query = " ".join(
            item
            for item in [tool_input.title, tool_input.organizer, tool_input.link, tool_input.description[:120]]
            if item
        )
        results = self.provider.search(query, max_results=5)
        return SearchToolOutput(
            status="success",
            summary=f"围绕「{tool_input.title}」返回 {len(results)} 条本地规则结果。",
            query=query,
            results=results,
        )
