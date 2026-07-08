from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import get_settings


@dataclass
class ExternalSearchResult:
    title: str
    url: str
    snippet: str
    source: str = "bocha"
    raw: dict[str, Any] | None = None


@dataclass
class ExternalSearchResponse:
    status: str
    provider: str
    query: str
    results: list[ExternalSearchResult] = field(default_factory=list)
    error_message: str = ""


class ExternalSearchProvider:
    provider_name = "external"

    def search(self, query: str, count: int = 5) -> ExternalSearchResponse:
        raise NotImplementedError


class BochaSearchProvider(ExternalSearchProvider):
    provider_name = "bocha"

    def __init__(self) -> None:
        settings = get_settings()
        self.api_key = settings.bocha_api_key
        self.base_url = settings.bocha_base_url or "https://api.bochaai.com/v1/web-search"
        self.count = settings.bocha_search_count

    def search(self, query: str, count: int | None = None) -> ExternalSearchResponse:
        if not self.api_key:
            return ExternalSearchResponse(
                status="unavailable",
                provider=self.provider_name,
                query=query,
                error_message="BOCHA_API_KEY 未配置",
            )
        limit = max(1, min(count or self.count or 5, 10))
        payload = {"query": query, "count": limit}
        request = Request(
            self.base_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                data = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            return ExternalSearchResponse(
                status="failed",
                provider=self.provider_name,
                query=query,
                error_message=f"博查搜索失败：HTTP {exc.code}",
            )
        except (URLError, TimeoutError, json.JSONDecodeError) as exc:
            return ExternalSearchResponse(
                status="failed",
                provider=self.provider_name,
                query=query,
                error_message=f"博查搜索失败：{exc}",
            )
        return ExternalSearchResponse(
            status="success",
            provider=self.provider_name,
            query=query,
            results=self._extract_results(data)[:limit],
        )

    def _extract_results(self, data: dict[str, Any]) -> list[ExternalSearchResult]:
        candidates: Any = data
        for key in ["data", "webPages", "web_pages", "results"]:
            if isinstance(candidates, dict) and key in candidates:
                candidates = candidates[key]
        if isinstance(candidates, dict):
            for key in ["value", "items", "results", "list"]:
                if isinstance(candidates.get(key), list):
                    candidates = candidates[key]
                    break
        if not isinstance(candidates, list):
            return []
        results: list[ExternalSearchResult] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            title = str(item.get("name") or item.get("title") or "").strip()
            url = str(item.get("url") or item.get("link") or "").strip()
            snippet = str(item.get("snippet") or item.get("summary") or item.get("description") or "").strip()
            if not title and not url and not snippet:
                continue
            results.append(
                ExternalSearchResult(
                    title=title,
                    url=url,
                    snippet=snippet,
                    source=self.provider_name,
                    raw={key: item.get(key) for key in ["name", "title", "url", "snippet", "summary", "siteName"] if key in item},
                )
            )
        return results
