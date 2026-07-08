from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.entities import EnrichmentResult, Opportunity, UserProfile
from app.services.growth_memory.memory_manager import GrowthMemoryManager
from app.services.json_utils import dumps
from app.services.llm_gateway import LLMGateway
from app.services.search_provider import SearchResult, WeixinSearchProvider
from app.services.tool_logging import record_tool_call


class SearchEnrichmentService:
    tool_name = "enrich_opportunity"

    def __init__(self, provider: WeixinSearchProvider | None = None, gateway: LLMGateway | None = None) -> None:
        self.provider = provider or WeixinSearchProvider()
        self.gateway = gateway or LLMGateway()

    def get_latest(self, db: Session, user_id: int, opportunity_id: int) -> EnrichmentResult | None:
        return (
            db.query(EnrichmentResult)
            .filter(EnrichmentResult.user_id == user_id, EnrichmentResult.opportunity_id == opportunity_id)
            .order_by(EnrichmentResult.created_at.desc())
            .first()
        )

    def enrich(self, db: Session, user_id: int, opportunity_id: int) -> EnrichmentResult:
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
            parsed = self._summarize(opportunity, sources)
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
                error_message=f"LLM 总结失败：{exc}",
                payload={"queries": queries, "sources": sources},
                result={"status": "llm_error"},
            )
            db.commit()
            raise RuntimeError(f"llm_error: LLM 总结失败：{exc}") from exc

        result = EnrichmentResult(
            user_id=user_id,
            opportunity_id=opportunity_id,
            query=query_text,
            summary=str(parsed.get("summary") or ""),
            key_findings=dumps(self._as_list(parsed.get("key_findings"))),
            official_links=dumps(self._as_list(parsed.get("official_links"))),
            registration_links=dumps(self._as_list(parsed.get("registration_links"))),
            deadline_notes=str(parsed.get("deadline_notes") or ""),
            eligibility_notes=str(parsed.get("eligibility_notes") or ""),
            risk_flags=dumps(self._as_list(parsed.get("risk_flags"))),
            credibility_score=self._as_float(parsed.get("credibility_score")),
            action_suggestion=str(parsed.get("action_suggestion") or ""),
            sources=dumps(sources),
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
            result_summary=result.summary[:500],
            payload={"queries": queries},
            result={"summary": result.summary, "source_count": len(sources)},
        )
        db.commit()
        db.refresh(result)
        GrowthMemoryManager().log_event(
            db,
            user_id=user_id,
            event_type="enrich_opportunity",
            event_target=str(opportunity_id),
            event_metadata={
                "opportunity_id": opportunity_id,
                "title": opportunity.name,
                "category": opportunity.category,
                "summary": result.summary[:500],
                "source_count": len(sources),
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
        queries = [
            f"{opportunity.name} 报名",
            f"{opportunity.organizer} {opportunity.name}".strip(),
        ]
        return [query for query in dict.fromkeys(queries) if query and not query.startswith("未注明 ")][:2]

    def _summarize(self, opportunity: Opportunity, sources: list[dict[str, Any]]) -> dict[str, Any]:
        system = "你是机会信息核验与补全助手。只输出 JSON 对象，不要 Markdown，不要额外解释。"
        user = f"""
请基于微信搜索结果，为当前机会做深挖详情总结。只能使用给定 sources 和 opportunity 字段，不确定就写“不确定”。
输出 JSON：
{{
  "summary": "",
  "key_findings": [],
  "official_links": [],
  "registration_links": [],
  "deadline_notes": "",
  "eligibility_notes": "",
  "risk_flags": [],
  "credibility_score": 0,
  "action_suggestion": ""
}}
credibility_score 为 0 到 100。

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

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            cleaned = match.group(0)
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            raise RuntimeError("LLM output is not a JSON object")
        return parsed

    def _as_list(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else ([] if value in (None, "") else [value])

    def _as_float(self, value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
