from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import Opportunity, SimilarOpportunityResult, UserProfile
from app.services.growth_memory.memory_manager import GrowthMemoryManager
from app.services.json_utils import dumps
from app.services.llm_gateway import LLMGateway
from app.services.search_provider import SearchResult, WeixinSearchProvider
from app.services.tool_logging import record_tool_call


class SimilarOpportunitySearchService:
    tool_name = "search_similar_opportunities"

    def __init__(self, provider: WeixinSearchProvider | None = None, gateway: LLMGateway | None = None) -> None:
        self.provider = provider or WeixinSearchProvider()
        self.gateway = gateway or LLMGateway()

    def get_latest(self, db: Session, user_id: int, opportunity_id: int) -> SimilarOpportunityResult | None:
        return (
            db.query(SimilarOpportunityResult)
            .filter(SimilarOpportunityResult.user_id == user_id, SimilarOpportunityResult.opportunity_id == opportunity_id)
            .order_by(SimilarOpportunityResult.created_at.desc())
            .first()
        )

    def search_similar(self, db: Session, user_id: int, opportunity_id: int) -> SimilarOpportunityResult:
        opportunity = self._load_context(db, user_id, opportunity_id)
        queries = self._build_queries(opportunity)
        query_text = " | ".join(queries)
        search_results: list[SearchResult] = []
        last_empty_message = ""
        for query in queries:
            response = self.provider.search(query, top_k=3)
            if response.status == "empty_results":
                last_empty_message = response.error_message
                continue
            if response.status != "success":
                record_tool_call(
                    db,
                    user_id=user_id,
                    opportunity_id=opportunity_id,
                    tool_name=self.tool_name,
                    query=query_text,
                    provider=response.provider,
                    status=response.status,
                    error_message=response.error_message,
                    payload={"queries": queries},
                    result={"status": response.status, "error_message": response.error_message},
                )
                db.commit()
                raise RuntimeError(f"{response.status}: {response.error_message}")
            search_results.extend(response.results)
        if not search_results:
            error_message = last_empty_message or "微信搜索结果为空"
            record_tool_call(
                db,
                user_id=user_id,
                opportunity_id=opportunity_id,
                tool_name=self.tool_name,
                query=query_text,
                provider=self.provider.provider_name,
                status="empty_results",
                error_message=error_message,
                payload={"queries": queries},
                result={"status": "empty_results", "error_message": error_message},
            )
            db.commit()
            raise RuntimeError(f"empty_results: {error_message}")

        sources = self._sources(search_results)
        try:
            parsed = self._compare(opportunity, sources)
        except Exception as exc:
            db.rollback()
            record_tool_call(
                db,
                user_id=user_id,
                opportunity_id=opportunity_id,
                tool_name=self.tool_name,
                query=query_text,
                provider=self.provider.provider_name,
                status="llm_error",
                error_message=f"LLM 结构化比较失败：{exc}",
                payload={"queries": queries, "sources": sources},
                result={"status": "llm_error"},
            )
            db.commit()
            raise RuntimeError(f"llm_error: LLM 结构化比较失败：{exc}") from exc

        results = self._normalize_results(parsed.get("results"))
        result = SimilarOpportunityResult(
            user_id=user_id,
            opportunity_id=opportunity_id,
            query=query_text,
            results=dumps(results),
            comparison_summary=str(parsed.get("comparison_summary") or ""),
            status="success",
        )
        db.add(result)
        record_tool_call(
            db,
            user_id=user_id,
            opportunity_id=opportunity_id,
            tool_name=self.tool_name,
            query=query_text,
            provider=self.provider.provider_name,
            status="success",
            result_summary=result.comparison_summary[:500],
            payload={"queries": queries},
            result={"comparison_summary": result.comparison_summary, "result_count": len(results)},
        )
        db.commit()
        db.refresh(result)
        GrowthMemoryManager().log_event(
            db,
            user_id=user_id,
            event_type="search_similar_opportunities",
            event_target=str(opportunity_id),
            event_metadata={
                "opportunity_id": opportunity_id,
                "title": opportunity.name,
                "category": opportunity.category,
                "comparison_summary": result.comparison_summary[:500],
                "result_count": len(results),
            },
        )
        return result

    def _load_context(self, db: Session, user_id: int, opportunity_id: int) -> Opportunity:
        if not db.get(UserProfile, user_id):
            raise ValueError("用户画像不存在")
        opportunity = db.get(Opportunity, opportunity_id)
        if not opportunity:
            raise ValueError("机会不存在")
        return opportunity

    def _build_queries(self, opportunity: Opportunity) -> list[str]:
        keywords = " ".join(
            item
            for item in [
                opportunity.category,
                opportunity.organizer,
                self._short_keywords(opportunity.name),
                opportunity.requirements[:80],
                opportunity.target_audience[:80],
            ]
            if item
        )
        queries = [
            f"{keywords} 实习 招募",
        ]
        return [query for query in dict.fromkeys(queries) if query.strip()][:1]

    def _compare(self, opportunity: Opportunity, sources: list[dict[str, Any]]) -> dict[str, Any]:
        system = "你是机会检索结果比较助手。只输出 JSON 对象，不要 Markdown，不要额外解释。"
        user = f"""
请从 sources 中识别与当前 opportunity 类似的外部机会，并做结构化对比。
不要把结果写入机会库；只输出 JSON：
{{
  "comparison_summary": "",
  "results": [
    {{
      "title": "",
      "organizer": "",
      "category": "",
      "deadline": "",
      "link": "",
      "source": "",
      "similarity_reason": "",
      "pros": [],
      "cons": [],
      "fit_score": 0
    }}
  ]
}}
fit_score 为 0 到 100。无法判断的字段写空字符串或空数组。

opportunity：{json.dumps(self._opportunity_dict(opportunity), ensure_ascii=False)}
sources：{json.dumps(sources[:20], ensure_ascii=False)}
"""
        text = self.gateway.chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.2,
        )
        return self._parse_json_object(text)

    def _sources(self, results: list[SearchResult]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        sources: list[dict[str, Any]] = []
        for item in results:
            key = item.url or item.title
            if not key or key in seen:
                continue
            seen.add(key)
            sources.append(
                {
                    "title": item.title,
                    "url": item.url,
                    "snippet": item.snippet,
                    "source": item.source,
                    "provider": item.provider,
                    "published_at": item.published_at,
                }
            )
        return sources

    def _normalize_results(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        normalized: list[dict[str, Any]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            normalized.append(
                {
                    "title": str(item.get("title") or ""),
                    "organizer": str(item.get("organizer") or ""),
                    "category": str(item.get("category") or ""),
                    "deadline": str(item.get("deadline") or ""),
                    "link": str(item.get("link") or ""),
                    "source": str(item.get("source") or ""),
                    "similarity_reason": str(item.get("similarity_reason") or ""),
                    "pros": item.get("pros") if isinstance(item.get("pros"), list) else [],
                    "cons": item.get("cons") if isinstance(item.get("cons"), list) else [],
                    "fit_score": self._score(item.get("fit_score")),
                }
            )
        return normalized

    def _opportunity_dict(self, opportunity: Opportunity) -> dict[str, Any]:
        return {
            "id": opportunity.id,
            "name": opportunity.name,
            "category": opportunity.category,
            "organizer": opportunity.organizer,
            "deadline": opportunity.deadline,
            "location": opportunity.location,
            "target_audience": opportunity.target_audience,
            "requirements": opportunity.requirements,
            "summary": opportunity.summary,
            "link": opportunity.link,
        }

    def _short_keywords(self, title: str) -> str:
        return " ".join(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", title)[:6])

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            cleaned = match.group(0)
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise RuntimeError("LLM output is not a JSON object")
        return parsed

    def _score(self, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0
