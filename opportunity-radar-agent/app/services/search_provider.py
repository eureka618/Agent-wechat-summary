from __future__ import annotations

import asyncio
import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import SearchCache
from app.services.json_utils import dumps


REQUIRED_WEIXIN_TOOLS = {"weixin_search", "weixin_search_all", "get_weixin_article_content"}


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str
    provider: str
    published_at: str | None = None
    raw: dict[str, Any] | None = None


@dataclass
class SearchProviderResponse:
    status: str
    provider: str
    query: str
    results: list[SearchResult] = field(default_factory=list)
    error_message: str = ""
    raw_item_count: int = 0
    mapped_item_count: int = 0
    cache_hit: bool = False
    cooldown_active: bool = False


@dataclass
class MCPDiagnosticResult:
    status: str
    url: str
    reachable: bool
    tools: list[str] = field(default_factory=list)
    missing_tools: list[str] = field(default_factory=list)
    error_message: str = ""
    cache_count: int = 0
    last_search_status: str = ""
    cooldown_until: str = ""
    cooldown_reason: str = ""


class MCPProviderError(RuntimeError):
    def __init__(self, status: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


class WeixinSearchProvider:
    provider_name = "weixin_search_mcp"
    cooldown_query = "__cooldown__"
    limited_statuses = {"mcp_server_unreachable", "rate_limited", "anti_scrape", "temporarily_limited"}

    def __init__(
        self,
        url: str | None = None,
        transport: str | None = None,
        config_path: str | None = None,
        server_name: str | None = None,
    ) -> None:
        settings = get_settings()
        self.url = url if url is not None else settings.weixin_search_mcp_url
        self.transport = (transport if transport is not None else settings.weixin_search_mcp_transport).lower()
        self.config_path = config_path if config_path is not None else settings.weixin_search_mcp_config
        self.server_name = server_name if server_name is not None else settings.weixin_search_mcp_server

    def diagnose(self) -> MCPDiagnosticResult:
        cache_info = self.cache_diagnostics()
        if not self.url and self.transport == "http":
            return MCPDiagnosticResult(
                status="not_configured",
                url="",
                reachable=False,
                error_message="WEIXIN_SEARCH_MCP_URL 未配置",
                **cache_info,
            )
        try:
            tools = asyncio.run(self._list_tools_async())
        except ImportError as exc:
            return MCPDiagnosticResult(
                status="not_configured",
                url=self.url,
                reachable=False,
                error_message=f"缺少 MCP 客户端依赖：{exc}",
                **cache_info,
            )
        except Exception as exc:
            return MCPDiagnosticResult(
                status="mcp_server_unreachable",
                url=self.url,
                reachable=False,
                error_message=f"MCP 服务不可达：{exc}",
                **cache_info,
            )

        missing = sorted(REQUIRED_WEIXIN_TOOLS.difference(tools))
        if missing:
            return MCPDiagnosticResult(
                status="mcp_tool_not_found",
                url=self.url,
                reachable=True,
                tools=sorted(tools),
                missing_tools=missing,
                error_message=f"MCP 工具不存在：{', '.join(missing)}",
                **cache_info,
            )
        return MCPDiagnosticResult(status="success", url=self.url, reachable=True, tools=sorted(tools), **cache_info)

    def search(
        self,
        query: str,
        top_k: int | None = None,
        max_results: int | None = None,
    ) -> SearchProviderResponse:
        settings = get_settings()
        limit = top_k or max_results or settings.weixin_search_max_results
        cached = self._read_cache(query, limit, completed_only=True)
        if cached:
            return cached
        cooldown = self._active_cooldown()
        if cooldown:
            return SearchProviderResponse(
                status="weixin_search_temporarily_limited",
                provider=self.provider_name,
                query=query,
                error_message="微信搜索暂时没有稳定结果，请稍后重试，或手动粘贴公众号文章链接。",
                cache_hit=True,
                cooldown_active=True,
            )
        cached = self._read_cache(query, limit)
        if cached:
            return cached
        if not self.url and self.transport == "http":
            response = SearchProviderResponse(
                status="not_configured",
                provider=self.provider_name,
                query=query,
                error_message="WEIXIN_SEARCH_MCP_URL 未配置",
            )
            self._write_cache(query, response)
            return response
        try:
            results, raw_item_count = asyncio.run(self._search_async(query))
        except ImportError as exc:
            response = SearchProviderResponse(
                status="not_configured",
                provider=self.provider_name,
                query=query,
                error_message=f"缺少 MCP 客户端依赖：{exc}",
            )
            self._write_cache(query, response)
            return response
        except MCPProviderError as exc:
            status = self._classify_error(exc.status, exc.message)
            response = SearchProviderResponse(
                status=status,
                provider=self.provider_name,
                query=query,
                error_message=exc.message,
            )
            self._write_cache(query, response)
            self._maybe_start_cooldown(response)
            return response
        except Exception as exc:
            response = SearchProviderResponse(
                status="mcp_server_unreachable",
                provider=self.provider_name,
                query=query,
                error_message=f"MCP 服务不可达：{exc}",
            )
            self._write_cache(query, response)
            self._maybe_start_cooldown(response)
            return response
        if not results:
            response = SearchProviderResponse(
                status="empty_results",
                provider=self.provider_name,
                query=query,
                error_message="微信搜索结果为空",
                raw_item_count=raw_item_count if "raw_item_count" in locals() else 0,
                mapped_item_count=0,
            )
            self._write_cache(query, response)
            self._maybe_start_cooldown(response)
            return response
        mapped = results[:limit]
        response = SearchProviderResponse(
            status="success",
            provider=self.provider_name,
            query=query,
            results=mapped,
            raw_item_count=raw_item_count,
            mapped_item_count=len(mapped),
        )
        self._write_cache(query, response)
        return response

    def cache_diagnostics(self) -> dict[str, str | int]:
        now = datetime.utcnow()
        db = SessionLocal()
        try:
            cache_count = db.query(SearchCache).filter(SearchCache.provider == self.provider_name).count()
            last = (
                db.query(SearchCache)
                .filter(SearchCache.provider == self.provider_name, SearchCache.query != self.cooldown_query)
                .order_by(SearchCache.created_at.desc())
                .first()
            )
            cooldown = (
                db.query(SearchCache)
                .filter(
                    SearchCache.provider == self.provider_name,
                    SearchCache.query == self.cooldown_query,
                    SearchCache.expires_at > now,
                )
                .order_by(SearchCache.created_at.desc())
                .first()
            )
            return {
                "cache_count": cache_count,
                "last_search_status": last.status if last else "",
                "cooldown_until": cooldown.expires_at.isoformat() if cooldown else "",
                "cooldown_reason": cooldown.error_message if cooldown else "",
            }
        finally:
            db.close()

    def raw_search_summary(self, query: str, page: int = 1) -> dict[str, Any]:
        if not self.url and self.transport == "http":
            return {
                "status": "not_configured",
                "error_message": "WEIXIN_SEARCH_MCP_URL 未配置",
                "raw_type": "",
                "raw_keys": [],
                "item_count": 0,
                "first_item_keys": [],
                "first_item_preview": {},
            }
        try:
            payload = asyncio.run(self._raw_search_async(query, page))
        except ImportError as exc:
            return {
                "status": "not_configured",
                "error_message": f"缺少 MCP 客户端依赖：{exc}",
                "raw_type": "",
                "raw_keys": [],
                "item_count": 0,
                "first_item_keys": [],
                "first_item_preview": {},
            }
        except MCPProviderError as exc:
            return {
                "status": exc.status,
                "error_message": exc.message,
                "raw_type": "",
                "raw_keys": [],
                "item_count": 0,
                "first_item_keys": [],
                "first_item_preview": {},
            }
        except Exception as exc:
            return {
                "status": "mcp_server_unreachable",
                "error_message": f"MCP 服务不可达：{exc}",
                "raw_type": "",
                "raw_keys": [],
                "item_count": 0,
                "first_item_keys": [],
                "first_item_preview": {},
            }

        items = self._extract_items(payload)
        mapped_items = self._normalize_results(payload, query)
        raw_keys = sorted(payload.keys()) if isinstance(payload, dict) else []
        first_item = next((item for item in items if isinstance(item, dict)), None)
        first_item_keys = sorted(first_item.keys()) if first_item else []
        if items and not mapped_items:
            status = "mapping_error"
            error_message = "MCP 原始响应包含文章，但 WeixinSearchProvider 没有映射出 SearchResult"
        elif items:
            status = "success"
            error_message = ""
        else:
            status = "empty_results"
            error_message = "MCP 原始响应没有文章，可能是搜狗微信反爬或搜索源暂时为空"
        return {
            "status": status,
            "error_message": error_message,
            "raw_type": type(payload).__name__,
            "raw_keys": raw_keys[:30],
            "item_count": len(items),
            "mapped_item_count": len(mapped_items),
            "first_item_keys": first_item_keys[:30],
            "first_item_preview": self._safe_preview(first_item) if first_item else {},
        }

    def get_article_content(self, real_url: str, referer: str | None = None) -> dict[str, Any]:
        if not self.url and self.transport == "http":
            raise MCPProviderError("not_configured", "WEIXIN_SEARCH_MCP_URL 未配置")
        try:
            return asyncio.run(self._get_article_content_async(real_url, referer))
        except MCPProviderError:
            raise
        except Exception as exc:
            raise MCPProviderError("content_fetch_failed", f"获取微信文章正文失败：{exc}") from exc

    async def _list_tools_async(self) -> set[str]:
        async with self._session_scope() as session:
            await session.initialize()
            return {str(getattr(tool, "name", "")) for tool in (await session.list_tools()).tools}

    async def _search_async(self, query: str) -> tuple[list[SearchResult], int]:
        payload = await self._raw_search_async(query, page=1)
        raw_item_count = len(self._extract_items(payload))
        return self._normalize_results(payload, query), raw_item_count

    async def _raw_search_async(self, query: str, page: int) -> Any:
        async with self._session_scope() as session:
            await session.initialize()
            tools = {str(getattr(tool, "name", "")) for tool in (await session.list_tools()).tools}
            self._ensure_required_tools(tools, {"weixin_search"})
            result = await session.call_tool("weixin_search", {"query": query, "page": page})
            if getattr(result, "isError", False):
                raise MCPProviderError("mcp_server_unreachable", self._content_to_text(result.content) or "MCP 搜索工具返回错误")
            return self._content_to_payload(result.content)

    async def _get_article_content_async(self, real_url: str, referer: str | None) -> dict[str, Any]:
        async with self._session_scope() as session:
            await session.initialize()
            tools = {str(getattr(tool, "name", "")) for tool in (await session.list_tools()).tools}
            self._ensure_required_tools(tools, {"get_weixin_article_content"})
            arguments = {"real_url": real_url}
            if referer:
                arguments["referer"] = referer
            result = await session.call_tool("get_weixin_article_content", arguments)
            if getattr(result, "isError", False):
                raise MCPProviderError("content_fetch_failed", self._content_to_text(result.content) or "MCP 正文工具返回错误")
            payload = self._content_to_payload(result.content)
            if isinstance(payload, dict):
                return payload
            return {"content": payload}

    @asynccontextmanager
    async def _session_scope(self) -> AsyncIterator[Any]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamablehttp_client

        if self.transport == "http":
            if not self.url:
                raise MCPProviderError("not_configured", "WEIXIN_SEARCH_MCP_URL 未配置")
            async with streamablehttp_client(self.url) as (read, write, _get_session_id):
                async with ClientSession(read, write) as session:
                    yield session
            return

        if self.transport == "stdio":
            if not self.config_path:
                raise MCPProviderError("not_configured", "WEIXIN_SEARCH_MCP_CONFIG 未配置")
            server_name, server_config = self._load_stdio_config(Path(self.config_path))
            command = str(server_config.get("command") or "")
            if not command:
                raise MCPProviderError("not_configured", f"Server '{server_name}' 缺少 stdio command")
            args = server_config.get("args") or []
            if not isinstance(args, list):
                raise MCPProviderError("not_configured", f"Server '{server_name}' args 必须是数组")
            params = StdioServerParameters(command=command, args=[str(item) for item in args], env=server_config.get("env"))
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    yield session
            return

        raise MCPProviderError("not_configured", f"不支持的 MCP transport：{self.transport}")

    def _load_stdio_config(self, path: Path) -> tuple[str, dict[str, Any]]:
        with path.open("r", encoding="utf-8") as f:
            config = json.load(f)
        servers = config.get("mcpServers")
        if not isinstance(servers, dict) or not servers:
            raise MCPProviderError("not_configured", "Config 必须包含 mcpServers")
        if self.server_name and self.server_name in servers and isinstance(servers[self.server_name], dict):
            return self.server_name, servers[self.server_name]
        first_name = next(iter(servers))
        server = servers[first_name]
        if not isinstance(server, dict):
            raise MCPProviderError("not_configured", f"Server '{first_name}' 配置必须是对象")
        return first_name, server

    def _ensure_required_tools(self, tools: set[str], required: set[str]) -> None:
        missing = sorted(required.difference(tools))
        if missing:
            raise MCPProviderError("mcp_tool_not_found", f"MCP 工具不存在：{', '.join(missing)}")

    def _read_cache(self, query: str, limit: int, completed_only: bool = False) -> SearchProviderResponse | None:
        now = datetime.utcnow()
        db = SessionLocal()
        try:
            query_obj = db.query(SearchCache).filter(
                SearchCache.provider == self.provider_name,
                SearchCache.query == query,
                SearchCache.expires_at > now,
            )
            if completed_only:
                query_obj = query_obj.filter(SearchCache.status == "completed")
            cache = query_obj.order_by(SearchCache.created_at.desc()).first()
            if not cache:
                return None
            results = self._results_from_cache(cache.results)[:limit]
            status = "success" if cache.status == "completed" else cache.status
            return SearchProviderResponse(
                status=status,
                provider=self.provider_name,
                query=query,
                results=results,
                error_message=cache.error_message,
                raw_item_count=len(results),
                mapped_item_count=len(results),
                cache_hit=True,
                cooldown_active=bool(self._active_cooldown(db)),
            )
        finally:
            db.close()

    def _write_cache(self, query: str, response: SearchProviderResponse) -> None:
        ttl = self._cache_ttl(response.status)
        if not ttl:
            return
        db = SessionLocal()
        try:
            db.add(
                SearchCache(
                    provider=self.provider_name,
                    query=query,
                    results=dumps([self._result_to_dict(item) for item in response.results]),
                    status="completed" if response.status == "success" else response.status,
                    error_message=response.error_message,
                    expires_at=datetime.utcnow() + ttl,
                )
            )
            db.commit()
        finally:
            db.close()

    def _cache_ttl(self, status: str) -> timedelta | None:
        if status == "success":
            return timedelta(hours=24)
        if status == "empty_results":
            return timedelta(minutes=20)
        if status in self.limited_statuses or status in {"weixin_search_temporarily_limited", "anti_scrape", "rate_limited"}:
            return timedelta(minutes=20)
        if status in {"not_configured", "mcp_tool_not_found"}:
            return timedelta(minutes=10)
        return None

    def _active_cooldown(self, db: Any | None = None) -> SearchCache | None:
        now = datetime.utcnow()
        owns_session = db is None
        db = db or SessionLocal()
        try:
            return (
                db.query(SearchCache)
                .filter(
                    SearchCache.provider == self.provider_name,
                    SearchCache.query == self.cooldown_query,
                    SearchCache.expires_at > now,
                )
                .order_by(SearchCache.created_at.desc())
                .first()
            )
        finally:
            if owns_session:
                db.close()

    def _maybe_start_cooldown(self, response: SearchProviderResponse) -> None:
        reason = ""
        if response.status in {"anti_scrape", "rate_limited", "temporarily_limited"}:
            reason = response.error_message or response.status
        elif self._looks_limited(response.error_message):
            reason = response.error_message
        elif response.status == "empty_results" and self._recent_empty_count() >= 2:
            reason = "短时间内多次微信搜索原始响应为空"
        elif response.status in self.limited_statuses:
            reason = response.error_message or response.status
        if not reason:
            return
        db = SessionLocal()
        try:
            db.add(
                SearchCache(
                    provider=self.provider_name,
                    query=self.cooldown_query,
                    results="[]",
                    status="cooldown",
                    error_message=reason[:500],
                    expires_at=datetime.utcnow() + timedelta(minutes=20),
                )
            )
            db.commit()
        finally:
            db.close()

    def _recent_empty_count(self) -> int:
        db = SessionLocal()
        try:
            since = datetime.utcnow() - timedelta(minutes=10)
            return (
                db.query(SearchCache)
                .filter(
                    SearchCache.provider == self.provider_name,
                    SearchCache.query != self.cooldown_query,
                    SearchCache.status == "empty_results",
                    SearchCache.created_at >= since,
                )
                .count()
            )
        finally:
            db.close()

    def _classify_error(self, status: str, message: str) -> str:
        if self._looks_limited(message):
            return "anti_scrape"
        return status

    def _looks_limited(self, message: str) -> bool:
        lowered = (message or "").lower()
        return any(token in lowered for token in ["反爬", "验证", "captcha", "blocked", "验证码", "too many", "rate limit"])

    def _result_to_dict(self, item: SearchResult) -> dict[str, Any]:
        return {
            "title": item.title,
            "url": item.url,
            "snippet": item.snippet,
            "source": item.source,
            "provider": item.provider,
            "published_at": item.published_at,
            "raw": item.raw,
        }

    def _results_from_cache(self, value: str) -> list[SearchResult]:
        try:
            parsed = json.loads(value or "[]")
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []
        results: list[SearchResult] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    url=str(item.get("url") or ""),
                    snippet=str(item.get("snippet") or ""),
                    source=str(item.get("source") or "微信公众号 / 搜狗微信"),
                    provider=str(item.get("provider") or self.provider_name),
                    published_at=item.get("published_at"),
                    raw=item.get("raw") if isinstance(item.get("raw"), dict) else None,
                )
            )
        return results

    def _normalize_results(self, content: Any, query: str) -> list[SearchResult]:
        payload = self._content_to_payload(content)
        items = self._extract_items(payload)
        normalized: list[SearchResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            title = self._first_text(item, ["title", "article_title", "name", "display_title"])
            url = self._pick_url(item)
            snippet = self._first_text(item, ["snippet", "summary", "desc", "digest", "content", "abstract"])
            source = self._first_text(item, ["source", "account", "account_name", "author", "publisher"]) or "微信公众号 / 搜狗微信"
            published_at = self._normalize_published_at(
                self._first_text(item, ["published_at", "publish_time", "date", "time", "datetime"])
            )
            if not title and not url and not snippet:
                continue
            normalized.append(
                SearchResult(
                    title=title or query,
                    url=url,
                    snippet=snippet,
                    source=source,
                    provider=self.provider_name,
                    published_at=published_at or None,
                    raw=item,
                )
            )
        return normalized

    def _safe_preview(self, item: dict[str, Any] | None) -> dict[str, Any]:
        if not item:
            return {}
        preview: dict[str, Any] = {}
        for key, value in item.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                text = "" if value is None else str(value)
                preview[key] = text[:240]
            elif isinstance(value, list):
                preview[key] = f"list[{len(value)}]"
            elif isinstance(value, dict):
                preview[key] = f"dict[{len(value)}]"
            else:
                preview[key] = type(value).__name__
            if len(preview) >= 12:
                break
        return preview

    def _pick_url(self, item: dict[str, Any]) -> str:
        candidates = [
            item.get("real_url"),
            item.get("realUrl"),
            item.get("article_url"),
            item.get("articleUrl"),
            item.get("mp_url"),
            item.get("url"),
            item.get("link"),
            item.get("sogou_url"),
        ]
        texts = [str(value).strip() for value in candidates if value]
        for value in texts:
            if "mp.weixin.qq.com" in value:
                return value
        return texts[0] if texts else ""

    def _first_text(self, item: dict[str, Any], keys: list[str]) -> str:
        for key in keys:
            value = item.get(key)
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
        return ""

    def _normalize_published_at(self, value: str) -> str:
        if not value:
            return ""
        match = re.search(r"timeConvert\('(\d+)'\)", value)
        if match:
            try:
                return datetime.fromtimestamp(int(match.group(1))).strftime("%Y-%m-%d %H:%M:%S")
            except (OSError, ValueError):
                return value
        return value

    def _content_to_payload(self, content: Any) -> Any:
        if isinstance(content, dict):
            return content
        if isinstance(content, list) and all(isinstance(item, dict) for item in content):
            return content
        if isinstance(content, list):
            texts = []
            values = []
            for item in content:
                text = getattr(item, "text", None)
                if text:
                    texts.append(text)
                else:
                    values.append(item)
            if texts:
                raw_text = "\n".join(texts)
            else:
                return values
        else:
            raw_text = str(content)
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            return [{"title": raw_text[:120], "snippet": raw_text}]

    def _content_to_text(self, content: Any) -> str:
        payload = self._content_to_payload(content)
        if isinstance(payload, str):
            return payload
        return json.dumps(payload, ensure_ascii=False)

    def _extract_items(self, payload: Any) -> list[Any]:
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for key in ["results", "items", "data", "articles", "list"]:
                value = payload.get(key)
                if isinstance(value, list):
                    return value
                if isinstance(value, dict):
                    nested = self._extract_items(value)
                    if nested:
                        return nested
            return [payload]
        return []
