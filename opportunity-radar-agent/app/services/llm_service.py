"""Structured LLM tasks built on the unified LLMGateway."""

from __future__ import annotations

import json
import re
from typing import Any

from app.services.llm_gateway import LLMGateway


CATEGORIES = ["竞赛", "实习", "科研", "实验室招募", "讲座", "夏令营", "奖学金", "课程", "出国", "保研", "其他"]


class LLMService:
    def __init__(self) -> None:
        self.gateway = LLMGateway()

    def extract_opportunities(self, article_title: str, article_content: str) -> list[dict[str, Any]]:
        system = "你是信息抽取助手。只输出 JSON 数组，不要 Markdown，不要额外解释。"
        user = f"""
请从公众号文章中抽取对大学生发展有价值的机会。
字段：
name, category, organizer, event_time, deadline, location, target_audience,
link, requirements, summary, benefit, cost, credibility
category 只能是：{", ".join(CATEGORIES)}
benefit/cost/credibility 是 0 到 1 的数字。
如果没有机会，输出 []。

标题：{article_title}
正文：
{article_content[:8000]}
"""
        text = self.gateway.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
        )
        return self._parse_json_array(text)

    def summarize_recommendations(self, profile: dict[str, Any], recommendations: list[dict[str, Any]]) -> str:
        system = "你是个性化机会雷达 Agent。请输出简洁中文摘要。"
        user = f"""
请基于用户画像和推荐结果生成摘要。
输出结构：
1. 本期最值得关注
2. 为什么匹配
3. 下一步行动
4. 需要留意的截止时间

用户画像：{json.dumps(profile, ensure_ascii=False)}
推荐结果：{json.dumps(recommendations[:8], ensure_ascii=False)}
"""
        return self.gateway.chat(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
        )

    def analyze_recommendation(
        self,
        profile: dict[str, Any],
        opportunity: dict[str, Any],
        base_scores: dict[str, float],
    ) -> dict[str, Any]:
        system = "你是机会推荐排序助手。只输出 JSON 对象，不要 Markdown，不要额外解释。"
        user = f"""
请基于用户画像、机会内容和基础规则分数，输出推荐排序辅助信息。
必须输出 JSON：
{{
  "relevance_score": 0,
  "benefit_score": 0,
  "content_overview": "",
  "relevance_explanation": ""
}}
relevance_score 和 benefit_score 取值 0 到 100。
content_overview 请用 150 到 500 个中文字符，基于 article_content_excerpt 和机会字段改写出自然完整摘要。
要求：不要只写标题，不要重复标题，不要直接复制 opportunity.summary，不要在半句话中断。

用户画像：{json.dumps(profile, ensure_ascii=False)}
机会：{json.dumps(opportunity, ensure_ascii=False)}
基础分数：{json.dumps(base_scores, ensure_ascii=False)}
"""
        parsed: dict[str, Any] | None = None
        last_error: Exception | None = None
        for attempt in range(2):
            prompt = user
            if attempt:
                prompt += "\n\n重要：上一次输出不是合法 JSON 或摘要质量不合格。请只返回一个合法 JSON 对象，字符串内部不要使用未转义的双引号。"
            try:
                text = self.gateway.chat(
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.2,
                )
                parsed = self._parse_json_object(text)
                content_overview = str(parsed.get("content_overview", "")).strip()
                source_summary = str(opportunity.get("summary") or "").strip()
                title = str(opportunity.get("name") or "").strip()
                self._validate_content_overview(content_overview, source_summary, title)
                break
            except RuntimeError as exc:
                last_error = exc
        if parsed is None:
            raise RuntimeError("LLM validation failed: {}".format(last_error))
        content_overview = str(parsed.get("content_overview", "")).strip()
        return {
            "relevance_score": float(parsed.get("relevance_score", base_scores.get("relevance_score", 50))),
            "benefit_score": float(parsed.get("benefit_score", base_scores.get("benefit_score", 50))),
            "content_overview": content_overview,
            "relevance_explanation": str(parsed.get("relevance_explanation", "")),
        }

    def _validate_content_overview(self, overview: str, source_summary: str, title: str) -> None:
        if len(overview) < 80:
            raise RuntimeError("LLM content_overview is too short")
        if source_summary and overview == source_summary:
            raise RuntimeError("LLM content_overview copied opportunity.summary")
        if title and overview.startswith(f"{title} {title}"):
            raise RuntimeError("LLM content_overview repeats title")
        if overview.endswith(("，", "、", "；", "：", "和", "与", "的", "在", "为", "对")):
            raise RuntimeError("LLM content_overview appears truncated")

    def _parse_json_array(self, text: str) -> list[dict[str, Any]]:
        cleaned = text.strip()
        match = re.search(r"\[[\s\S]*\]", cleaned)
        if match:
            cleaned = match.group(0)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            fixed = self._repair_json(cleaned, "array")
            try:
                parsed = json.loads(fixed)
            except json.JSONDecodeError as exc:
                raise RuntimeError("LLM output is not valid JSON array") from exc
        if not isinstance(parsed, list):
            raise RuntimeError("LLM output is not a JSON array")
        return [item for item in parsed if isinstance(item, dict)]

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            cleaned = match.group(0)
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            fixed = self._repair_json(cleaned, "object")
            try:
                parsed = json.loads(fixed)
            except json.JSONDecodeError as exc:
                raise RuntimeError("LLM output is not valid JSON object") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("LLM output is not a JSON object")
        return parsed

    def _repair_json(self, text: str, expected: str) -> str:
        system = "你是 JSON 格式修复器。只输出修复后的 JSON，不要 Markdown，不要解释。"
        user = f"请把下面内容修复为合法 JSON {expected}。不要改变字段含义：\n{text[:4000]}"
        return self.gateway.chat(
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            temperature=0.0,
            max_retries=1,
            response_format={"type": "json_object"} if expected == "object" else None,
        )
