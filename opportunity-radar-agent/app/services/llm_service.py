import json
import re
from typing import Any

from openai import OpenAI

from app.core.config import get_settings


CATEGORIES = ["竞赛", "实习", "科研", "实验室招募", "讲座", "夏令营", "奖学金", "课程", "其他"]


class LLMService:
    def __init__(self) -> None:
        self.settings = get_settings()

    def extract_opportunities(self, article_title: str, article_content: str) -> list[dict[str, Any]]:
        if self._has_real_llm():
            try:
                return self._extract_with_openai(article_title, article_content)
            except Exception:
                return self._mock_extract(article_title, article_content)
        return self._mock_extract(article_title, article_content)

    def summarize_recommendations(self, profile: dict[str, Any], recommendations: list[dict[str, Any]]) -> str:
        if not recommendations:
            return "本期没有发现特别匹配的机会。建议继续导入新的公众号文章，或放宽兴趣/地点偏好。"
        if self._has_real_llm():
            try:
                return self._summarize_with_openai(profile, recommendations)
            except Exception:
                pass
        return self._mock_summary(profile, recommendations)

    def analyze_recommendation(
        self,
        profile: dict[str, Any],
        opportunity: dict[str, Any],
        base_scores: dict[str, float],
    ) -> dict[str, Any]:
        """Generate user-facing recommendation copy and optional score hints.

        This uses the configured provider in .env, so DeepSeek works through the
        same OpenAI-compatible client as extraction. Fallback is deterministic.
        """
        if self._has_real_llm():
            try:
                return self._analyze_recommendation_with_openai(profile, opportunity, base_scores)
            except Exception:
                pass
        return self._mock_recommendation_analysis(profile, opportunity, base_scores)

    def _has_real_llm(self) -> bool:
        provider = self.settings.llm_provider.lower()
        if provider == "openai":
            return bool(self.settings.openai_api_key)
        if provider == "deepseek":
            return bool(self.settings.deepseek_api_key)
        return False

    def _client_config(self) -> tuple[str, str, str]:
        provider = self.settings.llm_provider.lower()
        if provider == "deepseek":
            return (
                self.settings.deepseek_api_key,
                self.settings.deepseek_base_url,
                self.settings.deepseek_model,
            )
        return (
            self.settings.openai_api_key,
            self.settings.openai_base_url,
            self.settings.openai_model,
        )

    def _extract_with_openai(self, title: str, content: str) -> list[dict[str, Any]]:
        api_key, base_url, model = self._client_config()
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        prompt = f"""
你是信息抽取助手。请从公众号文章中抽取对大学生发展有价值的机会。
只输出 JSON 数组，不要 Markdown。
字段：
name, category, organizer, event_time, deadline, location, target_audience,
link, requirements, summary, benefit, cost, credibility
category 只能是：{", ".join(CATEGORIES)}
benefit/cost/credibility 是 0 到 1 的数字。

标题：{title}
正文：
{content[:8000]}
"""
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        text = response.choices[0].message.content or "[]"
        return self._parse_json_array(text)

    def _summarize_with_openai(self, profile: dict[str, Any], recommendations: list[dict[str, Any]]) -> str:
        api_key, base_url, model = self._client_config()
        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        prompt = f"""
你是个性化机会雷达 Agent。请基于用户画像和推荐结果生成简洁中文摘要。
输出结构：
1. 本期最值得关注
2. 为什么匹配
3. 下一步行动
4. 需要留意的截止时间

用户画像：{json.dumps(profile, ensure_ascii=False)}
推荐结果：{json.dumps(recommendations[:8], ensure_ascii=False)}
"""
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )
        return response.choices[0].message.content or ""

    def _analyze_recommendation_with_openai(
        self,
        profile: dict[str, Any],
        opportunity: dict[str, Any],
        base_scores: dict[str, float],
    ) -> dict[str, Any]:
        api_key, base_url, model = self._client_config()
        client = OpenAI(api_key=api_key, base_url=base_url)
        prompt = f"""
你是“个性化公众号机会筛选 Agent”的推荐分析模块。
请根据用户画像和单条机会，输出简洁、面向浏览者的一眼可懂分析。
只输出 JSON 对象，不要 Markdown，不要解释。

字段：
content_overview: 用一句话概括这篇文章/机会核心内容，尽可能短，但信息完整。
relevance_explanation: 只分析“文章提到的具体点”和“用户画像期待点”的对应关系，不要复述内容概括，不要以“内容概括”开头，不要提具体分数。
relevance_score: 0-100，表示该机会与用户需求的匹配程度。
benefit_score: 0-100，表示该机会对用户成长/申请/简历/科研的潜在收益。

要求：
- 不要编造正文没有的信息。
- 如果是资讯/泛讨论而非行动机会，要指出相关性较弱。
- content_overview 和 relevance_explanation 必须拆开：前者说文章是什么，后者说为什么匹配用户。
- relevance_explanation 建议写成“文章中的 A/B/C 对应用户画像中的 X/Y/Z”，如果匹配弱，要直接说明弱在哪里。
- relevance_score 和 benefit_score 可以参考 base_scores，但应基于文本语义校正。

用户画像：{json.dumps(profile, ensure_ascii=False)}
机会信息：{json.dumps(opportunity, ensure_ascii=False)}
基础规则分：{json.dumps(base_scores, ensure_ascii=False)}
"""
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        text = response.choices[0].message.content or "{}"
        return self._normalize_recommendation_analysis(self._parse_json_object(text), opportunity, base_scores)

    def _parse_json_object(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            cleaned = match.group(0)
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}

    def _normalize_recommendation_analysis(
        self,
        item: dict[str, Any],
        opportunity: dict[str, Any],
        base_scores: dict[str, float],
    ) -> dict[str, Any]:
        overview = str(item.get("content_overview") or "").strip()
        explanation = self._clean_relevance_explanation(str(item.get("relevance_explanation") or "").strip())
        fallback = self._mock_recommendation_analysis({}, opportunity, base_scores)
        return {
            "content_overview": overview or fallback["content_overview"],
            "relevance_explanation": explanation or fallback["relevance_explanation"],
            "relevance_score": self._bounded_score(item.get("relevance_score"), base_scores.get("relevance_score", 50)),
            "benefit_score": self._bounded_score(item.get("benefit_score"), base_scores.get("benefit_score", 50)),
        }

    def _clean_relevance_explanation(self, text: str) -> str:
        text = re.sub(r"^\s*(内容概括|概括)\s*[:：]\s*", "", text)
        text = re.sub(r"^.*?相关性\s*[:：]\s*", "", text)
        text = re.sub(r"^\s*相关性\s*[:：]\s*", "", text)
        return text.strip()

    def _bounded_score(self, value: Any, fallback: float) -> float:
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = float(fallback)
        return max(0, min(100, score))

    def _parse_json_array(self, text: str) -> list[dict[str, Any]]:
        cleaned = text.strip()
        match = re.search(r"\[[\s\S]*\]", cleaned)
        if match:
            cleaned = match.group(0)
        parsed = json.loads(cleaned)
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, dict)]

    def _mock_extract(self, title: str, content: str) -> list[dict[str, Any]]:
        blocks = self._split_blocks(title, content)
        opportunities: list[dict[str, Any]] = []
        for block in blocks:
            category = self._detect_category(block)
            if category == "其他" and not self._looks_like_opportunity(block):
                continue
            name = self._detect_name(block, title, category)
            opportunities.append(
                {
                    "name": name,
                    "category": category,
                    "organizer": self._match_after(block, ["主办方", "组织方", "单位", "实验室"]) or "未注明",
                    "event_time": self._match_after(block, ["活动时间", "项目时间", "实习时间", "时间"]) or "",
                    "deadline": self._match_after(block, ["截止", "报名截止", "申请截止", "DDL"]) or "",
                    "location": self._match_after(block, ["地点", "城市", "校区"]) or ("线上" if "线上" in block else ""),
                    "target_audience": self._match_after(block, ["适合人群", "面向对象", "招募对象", "申请对象"]) or "",
                    "link": self._detect_link(block),
                    "requirements": self._match_after(block, ["要求", "申请条件", "报名条件"]) or "",
                    "summary": block[:180].replace("\n", " "),
                    "benefit": self._estimate_benefit(category, block),
                    "cost": self._estimate_cost(block),
                    "credibility": 0.85 if self._detect_link(block) or "大学" in block or "学院" in block else 0.65,
                }
            )
        return opportunities[:10]

    def _split_blocks(self, title: str, content: str) -> list[str]:
        chunks = re.split(r"\n\s*\n|#{1,3}\s+", content)
        blocks = [chunk.strip() for chunk in chunks if len(chunk.strip()) > 20]
        if not blocks and content.strip():
            blocks = [content.strip()]
        if len(blocks) == 1:
            return [f"{title}\n{blocks[0]}"]
        return blocks

    def _detect_category(self, text: str) -> str:
        mapping = {
            "竞赛": ["竞赛", "比赛", "挑战赛", "大赛", "hackathon"],
            "实习": ["实习", "intern", "招聘", "岗位"],
            "科研": ["科研", "课题", "论文", "研究项目", "RA"],
            "实验室招募": ["实验室", "课题组", "导师", "招募", "助研"],
            "讲座": ["讲座", "沙龙", "论坛", "分享会", "报告"],
            "夏令营": ["夏令营", "暑期学校", "summer school"],
            "奖学金": ["奖学金", "资助", "助学金"],
            "课程": ["课程", "训练营", "workshop", "工作坊"],
        }
        lower = text.lower()
        for category, keywords in mapping.items():
            if any(keyword.lower() in lower for keyword in keywords):
                return category
        return "其他"

    def _looks_like_opportunity(self, text: str) -> bool:
        keywords = ["报名", "申请", "截止", "招募", "链接", "时间", "地点", "要求"]
        return sum(1 for keyword in keywords if keyword in text) >= 2

    def _detect_name(self, text: str, title: str, category: str) -> str:
        for line in text.splitlines():
            line = line.strip(" #*-\t")
            if 4 <= len(line) <= 60 and any(word in line for word in ["招募", "竞赛", "实习", "讲座", "夏令营", "项目", "课程"]):
                return line
        return title if title else f"未命名{category}机会"

    def _match_after(self, text: str, labels: list[str]) -> str:
        for label in labels:
            pattern = rf"{label}\s*[:：]\s*(.+)"
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()[:200]
        return ""

    def _detect_link(self, text: str) -> str:
        match = re.search(r"https?://[^\s)）]+", text)
        return match.group(0) if match else ""

    def _estimate_benefit(self, category: str, text: str) -> float:
        base = {
            "实习": 0.9,
            "科研": 0.85,
            "实验室招募": 0.82,
            "竞赛": 0.78,
            "夏令营": 0.86,
            "奖学金": 0.75,
            "课程": 0.65,
            "讲座": 0.55,
            "其他": 0.5,
        }[category]
        if any(word in text for word in ["名校", "国家级", "顶会", "保研", "转正", "推荐信"]):
            base += 0.08
        return min(base, 1.0)

    def _estimate_cost(self, text: str) -> float:
        cost = 0.4
        if "线下" in text or "异地" in text:
            cost += 0.2
        if "每周" in text or "长期" in text:
            cost += 0.15
        if "简历" in text or "面试" in text or "作品集" in text:
            cost += 0.1
        return min(cost, 1.0)

    def _mock_summary(self, profile: dict[str, Any], recommendations: list[dict[str, Any]]) -> str:
        lines = ["## 本期机会摘要", ""]
        name = profile.get("name", "你")
        lines.append(f"{name}，本期共筛出 {len(recommendations)} 个较匹配机会，优先关注前 3 个：")
        for index, item in enumerate(recommendations[:3], start=1):
            lines.append(
                f"{index}. {item['name']}（{item['category']}，{item['total_score']:.1f} 分）："
                f"{item['reason']} 下一步：{item['action_suggestion']}"
            )
        urgent = [item for item in recommendations if item.get("deadline")]
        if urgent:
            lines.append("")
            lines.append("需要留意的截止时间：")
            for item in urgent[:5]:
                lines.append(f"- {item['name']}：{item['deadline']}")
        return "\n".join(lines)

    def _mock_recommendation_analysis(
        self,
        profile: dict[str, Any],
        opportunity: dict[str, Any],
        base_scores: dict[str, float],
    ) -> dict[str, Any]:
        text = re.sub(r"\s+", " ", str(opportunity.get("summary") or opportunity.get("name") or "")).strip()
        name = str(opportunity.get("name") or "")
        text = text.replace(name, "").strip(" ：:-") or name
        goals = "、".join(profile.get("current_goals") or []) or "当前目标"
        category = opportunity.get("category") or "机会"
        return {
            "content_overview": text,
            "relevance_explanation": f"文章中的{category}主题、要求和行动信息需要对应用户画像中的{goals}、技能兴趣与时间地点偏好来判断；若原文缺少报名入口或明确截止时间，应先核验后再推进。",
            "relevance_score": self._bounded_score(base_scores.get("relevance_score"), 50),
            "benefit_score": self._bounded_score(base_scores.get("benefit_score"), 50),
        }
