"""Unified LLM Gateway.

All production LLM calls must go through LLMGateway.chat().
Supported providers:
    - modelarts
    - deepseek
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import get_settings


logger = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "modelarts": "deepseek-v4-flash",
    "deepseek": "deepseek-chat",
}


class LLMGatewayError(RuntimeError):
    error_code = "llm_error"
    retryable = False


class LLMTransientError(LLMGatewayError):
    error_code = "llm_transient"
    retryable = True


class LLMAuthError(LLMGatewayError):
    error_code = "llm_auth_error"


class LLMBadRequestError(LLMGatewayError):
    error_code = "llm_bad_request"


class LLMResponseFormatError(LLMGatewayError):
    error_code = "llm_response_format_error"


class LLMGateway:
    def __init__(self) -> None:
        self.settings = get_settings()

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
        max_retries: int = 2,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        provider = (self.settings.llm_provider or "").lower().strip()
        if provider == "mock":
            raise LLMBadRequestError("LLM_PROVIDER=mock is not allowed in production.")
        if provider not in {"modelarts", "deepseek"}:
            raise LLMBadRequestError("Unsupported LLM_PROVIDER: {}".format(provider))

        api_key, endpoint, model = self._provider_config(provider)
        if not api_key:
            raise LLMAuthError("{} API key is missing.".format(provider))
        self._log_config(provider, endpoint, model, bool(api_key))

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if response_format:
            payload["response_format"] = response_format

        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                logger.info("llm request provider=%s endpoint=%s model=%s attempt=%s", provider, endpoint, model, attempt + 1)
                data = self._post_json(endpoint, api_key, payload)
                content = self._extract_content(data)
                logger.info("llm response provider=%s model=%s chars=%s", provider, model, len(content))
                return content
            except LLMGatewayError as exc:
                last_error = exc
                logger.exception("llm request failed provider=%s endpoint=%s model=%s attempt=%s", provider, endpoint, model, attempt + 1)
                if exc.retryable and attempt < max_retries:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                break

        raise LLMGatewayError("LLM request failed after retries: {}".format(last_error))

    def _provider_config(self, provider: str) -> tuple[str, str, str]:
        if provider == "modelarts":
            return (
                self.settings.modelarts_api_key,
                self._chat_completions_url(self.settings.modelarts_base_url),
                self.settings.modelarts_model or self.settings.llm_model or DEFAULT_MODELS["modelarts"],
            )
        return (
            self.settings.deepseek_api_key,
            self._chat_completions_url(self.settings.deepseek_base_url),
            self.settings.llm_model or DEFAULT_MODELS["deepseek"],
        )

    def _chat_completions_url(self, base_url: str) -> str:
        base_url = base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return base_url + "/chat/completions"

    def debug_config(self) -> dict[str, Any]:
        provider = (self.settings.llm_provider or "").lower().strip()
        endpoint = ""
        model = ""
        api_key_exists = False
        if provider in {"modelarts", "deepseek"}:
            api_key, endpoint, model = self._provider_config(provider)
            api_key_exists = bool(api_key)
        return {
            "llm_provider": provider,
            "modelarts_base_url": self.settings.modelarts_base_url,
            "modelarts_endpoint": self._chat_completions_url(self.settings.modelarts_base_url),
            "modelarts_model": self.settings.modelarts_model or self.settings.llm_model or DEFAULT_MODELS["modelarts"],
            "llm_model": self.settings.llm_model,
            "active_endpoint": endpoint,
            "active_model": model,
            "api_key_exists": api_key_exists,
        }

    def _log_config(self, provider: str, endpoint: str, model: str, api_key_exists: bool) -> None:
        logger.info(
            "llm config provider=%s modelarts_base_url=%s model=%s api_key_exists=%s active_endpoint=%s",
            provider,
            self.settings.modelarts_base_url,
            model,
            api_key_exists,
            endpoint,
        )

    def _post_json(self, endpoint: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer {}".format(api_key),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            message = "LLM {} (HTTP {}): {}".format(self._http_error_reason(exc.code), exc.code, detail[:1000])
            if exc.code in {400}:
                raise LLMBadRequestError(message) from exc
            if exc.code in {401, 403}:
                raise LLMAuthError(message) from exc
            if exc.code in {429, 502, 503, 504}:
                raise LLMTransientError(message) from exc
            if exc.code >= 500:
                raise LLMTransientError(message) from exc
            raise LLMGatewayError(message) from exc
        except URLError as exc:
            raise LLMTransientError("LLM url error: {}".format(exc.reason)) from exc
        except TimeoutError as exc:
            raise LLMTransientError("LLM request timeout") from exc
        except socket.timeout as exc:
            raise LLMTransientError("LLM request timeout") from exc

    def _http_error_reason(self, status_code: int) -> str:
        if status_code in {401, 403}:
            return "unauthorized"
        if status_code == 404:
            return "url error or model not found"
        if status_code == 429:
            return "rate limited"
        if status_code >= 500:
            return "provider server error"
        return "http error"

    def _extract_content(self, data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            raise LLMResponseFormatError("LLM response format error: no choices")
        content = choices[0].get("message", {}).get("content", "")
        if isinstance(content, list):
            return "\n".join(str(item.get("text", item)) for item in content)
        content_text = str(content or "")
        if not content_text:
            raise LLMResponseFormatError("LLM response format error: empty message content")
        return content_text
