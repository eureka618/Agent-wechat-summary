from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from html import escape
from pathlib import Path

import requests
import streamlit as st


API_BASE = os.environ.get("API_BASE", "http://127.0.0.1:8000")


st.set_page_config(page_title="机会雷达 Agent", layout="wide")

st.title("个性化公众号机会雷达 Agent")

st.markdown(
    """
    <style>
    .rec-title {
        font-size: 1.35rem;
        font-weight: 750;
        line-height: 1.35;
        margin: 0.15rem 0 0.35rem 0;
    }
    .rec-meta {
        color: #4b5563;
        font-size: 0.98rem;
        font-weight: 650;
        margin-bottom: 0.35rem;
    }
    .rec-section {
        font-size: 1.08rem;
        font-weight: 750;
        margin-top: 0.85rem;
        margin-bottom: 0.2rem;
    }
    .rec-body {
        font-size: 1.03rem;
        line-height: 1.72;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


STATE_LABELS = {
    "new": "新机会",
    "recommended": "已推荐",
    "verified": "已核验",
    "saved": "已收藏",
    "todo_created": "已生成待办",
    "calendar_created": "已加入日历",
    "applied": "已申请",
    "archived": "已归档",
}


class APIClientError(Exception):
    pass


def state_label(state: str) -> str:
    return STATE_LABELS.get(state or "new", state or "新机会")


def recommendation_state_label(state: str) -> str:
    if (state or "") in {"", "new", "recommended", "verified", "saved"}:
        return "未申请"
    if state == "email_drafted":
        return "已处理"
    return state_label(state)


def html_text(value: object) -> str:
    return escape(str(value or ""))


def verification_badge(opp: dict) -> str:
    status = opp.get("verification_status") or ""
    risk = opp.get("risk_level") or ""
    if not status:
        return "未核验"
    if status == "verified":
        return f"已核验：风险 {risk}" if risk else "已核验"
    if status == "uncertain":
        return "信息不足：uncertain"
    if status == "suspicious":
        return f"风险较高：{risk}"
    return status


def clean_overview_text(text: object, title: object) -> str:
    cleaned = str(text or "").strip()
    title_text = str(title or "").strip()
    if not cleaned or not title_text:
        return cleaned

    compact_title = re.escape(title_text)
    pattern = rf"^({compact_title})(\s*[|｜:：\-—]*\s*)({compact_title})(\s*[|｜:：\-—]*\s*)"
    cleaned = re.sub(pattern, r"\3\4", cleaned, count=1)
    if cleaned.startswith(title_text):
        rest = cleaned[len(title_text):].lstrip()
        if rest.startswith(title_text):
            cleaned = rest
    return cleaned.strip()


def render_long_text(text: str, key: str) -> None:
    if len(text) <= 500:
        st.markdown(f"<div class='rec-body'>{html_text(text)}</div>", unsafe_allow_html=True)
        return

    preview = text[:300].rstrip()
    st.markdown(f"<div class='rec-body'>{html_text(preview)}...</div>", unsafe_allow_html=True)
    with st.expander("查看完整内容"):
        st.markdown(html_text(text).replace("\n", "  \n"))


def backend_health_ok() -> bool:
    try:
        return requests.get(f"{API_BASE}/health", timeout=2).ok
    except requests.RequestException:
        return False


def request_error_message(exc: requests.RequestException) -> str:
    if isinstance(exc, requests.Timeout):
        return "本次处理时间较长，请稍后重试。"
    if isinstance(exc, requests.ConnectionError) and not backend_health_ok():
        return "无法连接后端服务，请确认 FastAPI 已启动。"
    return "服务连接不稳定，请稍后重试。"


def api_get(path: str, timeout: int = 20):
    try:
        response = requests.get(f"{API_BASE}{path}", timeout=timeout)
    except requests.RequestException as exc:
        raise APIClientError(request_error_message(exc)) from exc
    return parse_api_response(response, path)


def api_post(path: str, payload: dict | None = None, timeout: int = 60):
    try:
        response = requests.post(f"{API_BASE}{path}", json=payload or {}, timeout=timeout)
    except requests.RequestException as exc:
        raise APIClientError(request_error_message(exc)) from exc
    return parse_api_response(response, path)


def api_put(path: str, payload: dict):
    try:
        response = requests.put(f"{API_BASE}{path}", json=payload, timeout=60)
    except requests.RequestException as exc:
        raise APIClientError(request_error_message(exc)) from exc
    return parse_api_response(response, path)


def api_patch(path: str, payload: dict):
    try:
        response = requests.patch(f"{API_BASE}{path}", json=payload, timeout=60)
    except requests.RequestException as exc:
        raise APIClientError(request_error_message(exc)) from exc
    return parse_api_response(response, path)


def api_delete(path: str):
    try:
        response = requests.delete(f"{API_BASE}{path}", timeout=20)
    except requests.RequestException as exc:
        raise APIClientError(request_error_message(exc)) from exc
    return parse_api_response(response, path)


def parse_api_response(response: requests.Response, path: str):
    if response.ok:
        if not response.content:
            return {}
        return response.json()

    detail = ""
    try:
        payload = response.json()
        raw_detail = payload.get("detail") or ""
        if isinstance(raw_detail, dict):
            error_code = raw_detail.get("error_code") or raw_detail.get("status") or "unknown_error"
            error_message = raw_detail.get("error_message") or raw_detail.get("message") or "服务处理失败"
            request_id = raw_detail.get("request_id") or ""
            if error_code in {"empty_results", "weixin_search_temporarily_limited", "mcp_server_unreachable"}:
                error_message = "微信搜索暂时没有稳定结果，请稍后重试，或手动粘贴公众号文章链接。"
            elif error_code == "database_busy":
                error_message = "系统正在处理其他任务，请稍后重试"
            elif error_code in {"model_unavailable", "llm_transient"}:
                error_message = "AI 服务暂时不可用"
            elif error_code == "model_json_error":
                error_message = "AI 返回格式异常"
            detail = f"{error_message}（request_id: {request_id}）" if request_id else f"{error_code}: {error_message}".strip()
        else:
            detail = str(raw_detail)
    except ValueError:
        detail = response.text.strip()

    if response.status_code == 404 and path.startswith("/recommendations"):
        raise APIClientError(detail or "推荐接口不存在或用户不存在")
    if response.status_code == 404:
        raise APIClientError(detail or "接口不存在或资源不存在")

    message = detail or f"接口请求失败，状态码 {response.status_code}"
    raise APIClientError(message)


def split_csv(text: str) -> list[str]:
    return [item.strip() for item in text.split(",") if item.strip()]


def join_csv(items: list[str]) -> str:
    return ", ".join(items or [])


def run_action(opportunity_id: int, user_id: int, action: str, extra_params: dict | None = None):
    return api_post(
        f"/opportunities/{opportunity_id}/actions",
        {"user_id": user_id, "action": action, "extra_params": extra_params or {}},
    )


def log_memory_event(user_id: int, event_type: str, opportunity_id: int, metadata: dict | None = None):
    return api_post(
        "/memory/events",
        {
            "user_id": user_id,
            "event_type": event_type,
            "event_target": str(opportunity_id),
            "event_metadata": metadata or {},
        },
    )


def get_latest_growth_memory(user_id: int):
    return api_get(f"/memory/{user_id}/latest")


def generate_growth_memory(user_id: int):
    return api_post(f"/memory/{user_id}/generate")


def create_todo_from_opportunity(user_id: int, opportunity_id: int):
    return api_post(f"/todos/from-opportunity/{user_id}/{opportunity_id}")


def enrich_opportunity(user_id: int, opportunity_id: int):
    return api_post(f"/tools/enrich/{user_id}/{opportunity_id}")


def search_similar_opportunities(user_id: int, opportunity_id: int):
    return api_post(f"/tools/similar/{user_id}/{opportunity_id}")


def ask_opportunity_assistant(
    user_id: int,
    opportunity_id: int,
    message: str,
    use_web_search: bool = False,
    include_memory: bool = True,
):
    return api_post(
        "/tools/opportunity-assistant/chat",
        {
            "user_id": user_id,
            "opportunity_id": opportunity_id,
            "message": message,
            "use_web_search": use_web_search,
            "include_memory": include_memory,
        },
    )


def analyze_opportunity_progress(user_id: int, opportunity_id: int):
    return api_post(f"/tools/opportunity-progress/{user_id}/{opportunity_id}/analyze", timeout=15)


def get_opportunity_progress_task(task_id: int):
    return api_get(f"/tools/opportunity-progress/tasks/{task_id}", timeout=15)


def get_latest_opportunity_progress_task(user_id: int, opportunity_id: int):
    return api_get(f"/tools/opportunity-progress/{user_id}/{opportunity_id}/task", timeout=15)


def confirm_opportunity_progress_todo(task_id: int):
    return api_post(f"/tools/opportunity-progress/{task_id}/confirm-todo")


def create_progress_plan_from_task(task_id: int):
    return api_post(f"/tools/opportunity-progress/{task_id}/create-plan")


def get_current_progress_plan(user_id: int, opportunity_id: int):
    return api_get(f"/tools/opportunity-progress/{user_id}/{opportunity_id}/plan")


def replan_progress_plan(plan_id: int, progress_text: str):
    return api_post(f"/tools/opportunity-progress/plans/{plan_id}/replan", {"progress_text": progress_text})


def confirm_progress_update(plan_id: int, update_id: int):
    return api_post(f"/tools/opportunity-progress/plans/{plan_id}/confirm-update", {"update_id": update_id})


def dismiss_progress_update(plan_id: int, update_id: int):
    return api_post(f"/tools/opportunity-progress/plans/{plan_id}/dismiss-update", {"update_id": update_id})


def sync_progress_items_to_todos(plan_id: int, item_ids: list[int]):
    return api_post(f"/tools/opportunity-progress/plans/{plan_id}/sync-todos", {"item_ids": item_ids})


def set_progress_plan_status(plan_id: int, action: str):
    return api_post(f"/tools/opportunity-progress/plans/{plan_id}/{action}")


def start_task_web_check(task_id: int):
    return api_post(f"/tools/opportunity-progress/tasks/{task_id}/web-check", timeout=15)


def start_plan_web_check(plan_id: int):
    return api_post(f"/tools/opportunity-progress/plans/{plan_id}/web-check", timeout=15)


def get_progress_web_check(check_id: int):
    return api_get(f"/tools/opportunity-progress/web-checks/{check_id}", timeout=15)


def get_latest_task_web_check(task_id: int):
    return api_get(f"/tools/opportunity-progress/tasks/{task_id}/web-check", timeout=15)


def get_latest_plan_web_check(plan_id: int):
    return api_get(f"/tools/opportunity-progress/plans/{plan_id}/web-check", timeout=15)


def send_recommendation_feedback(
    user_id: int,
    recommendation_id: int,
    opportunity_id: int,
    event_type: str,
    feedback_text: str = "",
):
    return api_post(
        f"/feedback/recommendation/{user_id}/{recommendation_id}",
        {
            "event_type": event_type,
            "opportunity_id": opportunity_id,
            "metadata": {
                "source": "recommendation_card",
                "feedback_type": event_type,
                "feedback_text": feedback_text.strip(),
            },
        },
    )


def mark_opportunity_applied(user_id: int, opportunity_id: int):
    return api_patch(f"/opportunity-states/{user_id}/{opportunity_id}", {"state": "applied"})


def get_calendar_dashboard(user_id: int):
    return api_get(f"/calendar/{user_id}")


def get_source_article(opportunity_id: int):
    return api_get(f"/opportunities/{opportunity_id}/source-article")


def format_calendar_time(value: str | None) -> str:
    if not value:
        return "未注明"
    text = str(value).replace("T", " ")
    return text[:16]


def calendar_date_key(value: str | None) -> str:
    if not value:
        return "日期未明"
    return str(value).replace("T", " ")[:10]


def calendar_time_text(value: str | None) -> str:
    if not value:
        return "--:--"
    return str(value).replace("T", " ")[11:16]


def calendar_sort_key(item: dict) -> str:
    return str(item.get("start_time") or "9999-12-31T23:59:59")


def build_ics(event: dict) -> str:
    if not event.get("start_time"):
        return ""
    start_text = str(event.get("start_time") or "").replace("-", "").replace(":", "").replace("T", "")
    start_text = start_text[:15]
    end_text = str(event.get("end_time") or event.get("start_time") or "").replace("-", "").replace(":", "").replace("T", "")
    end_text = end_text[:15] if end_text else start_text
    uid = f"opportunity-{event.get('id')}-{event.get('opportunity_id')}@opportunity-radar"
    summary = f"{event.get('reminder_type') or '时间提醒'}｜{event.get('short_title') or event.get('title') or '机会提醒'}"
    description = str(event.get("display_date_text") or "机会提醒")
    return "\n".join(
        [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Opportunity Radar Agent//Calendar//CN",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
            f"DTSTART:{start_text}",
            f"DTEND:{end_text}",
            f"SUMMARY:{summary}",
            f"DESCRIPTION:{description}",
            "END:VEVENT",
            "END:VCALENDAR",
        ]
    )


def render_calendar_event(event: dict, key_prefix: str) -> None:
    row_cols = st.columns([1.1, 1.2, 5, 1.4])
    row_cols[0].write(calendar_time_text(event.get("start_time")))
    row_cols[1].write(event.get("reminder_type") or "时间提醒")
    row_cols[2].write(event.get("short_title") or event.get("title") or "机会提醒")
    with row_cols[3]:
        st.download_button(
            "下载 .ics",
            data=build_ics(event),
            file_name=f"opportunity-{event.get('id')}.ics",
            mime="text/calendar",
            key=f"{key_prefix}-ics-{event.get('id')}",
        )


def render_calendar_grouped(events: list[dict], key_prefix: str) -> None:
    current_date = ""
    for event in events:
        date_key = calendar_date_key(event.get("start_time"))
        if date_key != current_date:
            current_date = date_key
            st.markdown(f"**{current_date}**")
        render_calendar_event(event, key_prefix)


def render_source_links(sources: list[dict], key_prefix: str) -> None:
    if not sources:
        st.caption("暂无来源链接")
        return
    for index, source in enumerate(sources[:8], start=1):
        title = source.get("title") or source.get("source") or f"来源 {index}"
        url = source.get("url") or source.get("link") or ""
        snippet = source.get("snippet") or ""
        if url:
            st.markdown(f"{index}. [{title}]({url})")
        else:
            st.markdown(f"{index}. {html_text(title)}")
        if snippet:
            st.caption(snippet[:180])


def render_enrichment_result(result: dict) -> None:
    st.markdown("<div class='rec-section'>补充背景</div>", unsafe_allow_html=True)
    st.write(result.get("summary") or "暂无补充背景")
    findings = result.get("key_findings") or []
    if findings:
        st.markdown("**关键线索**")
        for item in findings:
            st.write(f"- {item}")
    st.markdown("<div class='rec-section'>来源链接</div>", unsafe_allow_html=True)
    render_source_links(result.get("sources") or [], "enrich-source")
    st.markdown("<div class='rec-section'>截止时间线索</div>", unsafe_allow_html=True)
    st.write(result.get("deadline_notes") or "暂无明确截止时间线索")
    st.markdown("<div class='rec-section'>风险提示</div>", unsafe_allow_html=True)
    risks = result.get("risk_flags") or []
    st.write("；".join(str(item) for item in risks) if risks else "暂无明确风险提示")
    st.markdown("<div class='rec-section'>行动建议</div>", unsafe_allow_html=True)
    st.write(result.get("action_suggestion") or "暂无行动建议")


def render_similar_result(result: dict) -> None:
    st.markdown("<div class='rec-section'>简要对比</div>", unsafe_allow_html=True)
    st.write(result.get("comparison_summary") or "暂无简要对比")
    st.markdown("<div class='rec-section'>同类机会列表</div>", unsafe_allow_html=True)
    items = result.get("results") or []
    if not items:
        st.info("暂未识别到同类机会。")
        return
    for index, item in enumerate(items, start=1):
        title = item.get("title") or f"同类机会 {index}"
        with st.container(border=True):
            st.markdown(f"**{title}**")
            st.caption(
                "主办方：{organizer} | 类型：{category} | 截止：{deadline} | fit_score：{score}".format(
                    organizer=item.get("organizer") or "未注明",
                    category=item.get("category") or "未注明",
                    deadline=item.get("deadline") or "未注明",
                    score=item.get("fit_score", 0),
                )
            )
            st.write(item.get("similarity_reason") or "")
            if item.get("link"):
                st.markdown(f"[来源链接]({item['link']})")
            pros = item.get("pros") or []
            cons = item.get("cons") or []
            if pros:
                st.write("优势：" + "；".join(str(value) for value in pros))
            if cons:
                st.write("注意：" + "；".join(str(value) for value in cons))


ASSISTANT_PROMPTS = [
    "这个机会适合我吗？",
    "它和我的目标匹配吗？",
    "它有什么风险？",
    "我应该怎么准备？",
    "有没有类似但更偏工程落地的方向？",
    "帮我生成申请准备清单",
]


def render_opportunity_assistant(user_id: int, rec: dict, opp: dict, article_context: dict | None = None) -> None:
    rec_id = rec["id"]
    opp_id = opp["id"]
    history_key = f"assistant-history-{rec_id}-{opp_id}"
    open_key = f"assistant-open-{rec_id}-{opp_id}"
    if history_key not in st.session_state:
        st.session_state[history_key] = []
    with st.expander("问问机会助理", expanded=st.session_state.get(open_key, False)):
        st.markdown(f"**当前机会：** {opp.get('name') or '未命名机会'}")
        st.caption(
            "类别：{category} | 主办方：{organizer} | 截止：{deadline}".format(
                category=opp.get("category") or "未注明",
                organizer=opp.get("organizer") or "未注明",
                deadline=rec.get("deadline") or opp.get("deadline") or "未注明",
            )
        )
        st.write(opp.get("summary") or rec.get("content_overview") or "")
        use_web = st.toggle("启用联网搜索", value=False, key=f"assistant-web-{rec_id}-{opp_id}")

        prompt_cols = st.columns(3)
        for index, prompt in enumerate(ASSISTANT_PROMPTS):
            if prompt_cols[index % 3].button(prompt, key=f"assistant-prompt-{index}-{rec_id}-{opp_id}"):
                _send_assistant_message(user_id, opp_id, prompt, use_web, history_key, article_context)

        user_message = st.text_input("向机会助理提问", key=f"assistant-input-{rec_id}-{opp_id}", placeholder="例如：我应该怎么准备？")
        if st.button("发送", key=f"assistant-send-{rec_id}-{opp_id}") and user_message.strip():
            _send_assistant_message(user_id, opp_id, user_message.strip(), use_web, history_key, article_context)

        for item in st.session_state[history_key][-6:]:
            st.markdown(f"**你：** {html_text(item['user'])}")
            st.markdown(item["assistant"])
            sources = item.get("sources") or []
            if sources:
                with st.expander("来源链接"):
                    render_source_links(sources, f"assistant-source-{rec_id}-{opp_id}")


def _send_assistant_message(
    user_id: int,
    opportunity_id: int,
    message: str,
    use_web: bool,
    history_key: str,
    article_context: dict | None = None,
) -> None:
    try:
        outbound_message = message
        if article_context:
            excerpt = str(article_context.get("content") or "")[:1500]
            if excerpt:
                outbound_message = (
                    f"{message}\n\n"
                    f"补充原文上下文（仅供判断，不要逐字复述）：\n"
                    f"标题：{article_context.get('article_title') or article_context.get('opportunity_name') or ''}\n"
                    f"摘要/原文节选：{excerpt}"
                )
        result = ask_opportunity_assistant(
            user_id=user_id,
            opportunity_id=opportunity_id,
            message=outbound_message,
            use_web_search=use_web,
            include_memory=True,
        )
        st.session_state[history_key].append(
            {
                "user": message,
                "assistant": result.get("answer") or "",
                "sources": result.get("sources") or [],
                "memory_updated": result.get("memory_updated"),
            }
        )
    except APIClientError as exc:
        st.error(str(exc))


def render_progress_update(update: dict, plan_id: int, plan_key: str) -> None:
    changes = update.get("proposed_changes") or {}
    st.markdown("### AI 对最新进展的判断")
    st.write(changes.get("progress_assessment") or update.get("progress_assessment") or "AI 已生成调整建议。")
    st.markdown("### 建议保留的内容")
    keeps = changes.get("tasks_to_keep") or []
    if keeps:
        for item in keeps:
            st.write(f"- 任务 {item.get('task_id')}：{item.get('reason') or '继续保留'}")
    else:
        st.caption("暂无特别说明。")
    st.markdown("### 建议修改的内容")
    task_changes = changes.get("task_changes") or []
    if task_changes:
        for item in task_changes:
            st.write(f"- 任务 {item.get('task_id')}：{item.get('change_type')}。{item.get('reason') or ''}")
    else:
        st.caption("暂无建议修改的既有任务。")
    st.markdown("### 建议新增的任务")
    new_tasks = changes.get("new_tasks") or []
    if new_tasks:
        for item in new_tasks:
            st.write(f"- **{item.get('title')}**：{item.get('description') or item.get('reason') or ''}")
    else:
        st.caption("暂无新增任务建议。")
    st.markdown("### 可能不再适用的任务")
    obsolete = [item for item in task_changes if item.get("change_type") == "mark_no_longer_applicable"]
    if obsolete:
        for item in obsolete:
            st.write(f"- 任务 {item.get('task_id')}：{item.get('reason') or '可能不再适用'}")
    else:
        st.caption("暂无。")
    st.markdown("### 下一步重点")
    st.write(changes.get("suggested_next_focus") or "继续按当前计划推进。")
    completion = changes.get("completion_suggestion") or {}
    if completion.get("suggest_complete") or completion.get("suggest_pause") or completion.get("suggest_stop"):
        st.info(completion.get("reason") or "AI 建议你考虑调整推进状态，但最终由你确认。")
    update_cols = st.columns([1.4, 1.2, 4])
    if update_cols[0].button("确认更新计划", key=f"progress-confirm-update-{update.get('id')}"):
        try:
            result = confirm_progress_update(plan_id, int(update["id"]))
            st.session_state[plan_key] = result.get("plan")
            st.success(result.get("message") or "推进计划已更新")
            st.rerun()
        except APIClientError as exc:
            st.error(str(exc))
    if update_cols[1].button("暂不修改", key=f"progress-dismiss-update-{update.get('id')}"):
        try:
            result = dismiss_progress_update(plan_id, int(update["id"]))
            st.session_state[plan_key] = result.get("plan")
            st.success(result.get("message") or "已保留原计划")
            st.rerun()
        except APIClientError as exc:
            st.error(str(exc))


def render_web_check_result(web_check: dict | None, owner_type: str, owner_id: int, state_key: str) -> None:
    status_labels = {
        "searching": "正在联网核实",
        "completed": "已完成联网分析",
        "partially_completed": "已保存部分联网结果",
        "failed": "联网失败，仍使用本地信息",
    }
    if not web_check:
        st.caption("当前结论基于本地信息")
        button_label = "联网核实最新信息"
    else:
        status = web_check.get("status") or "not_searched"
        st.caption(status_labels.get(status, "未联网"))
        button_label = "重新联网核实" if status in {"completed", "partially_completed", "failed"} else "联网核实最新信息"
        if status == "searching":
            try:
                latest = get_progress_web_check(int(web_check["id"]))
                st.session_state[state_key] = latest.get("web_check")
            except APIClientError as exc:
                st.warning(str(exc))
            time.sleep(2)
            st.rerun()
            return
        if status == "failed":
            st.warning("联网搜索暂时失败，当前仍基于本地信息分析。")
        analysis = web_check.get("analysis") or {}
        if analysis and status in {"completed", "partially_completed", "failed"}:
            st.markdown("**联网核实结论**")
            st.write(analysis.get("summary") or "暂无联网分析结论。")
            if analysis.get("official_source_assessment"):
                st.write("官方来源：" + str(analysis.get("official_source_assessment")))
            if analysis.get("deadline_update"):
                st.write("截止时间：" + str(analysis.get("deadline_update")))
            if analysis.get("registration_update"):
                st.write("报名入口：" + str(analysis.get("registration_update")))
            if analysis.get("plan_adjustment_suggestion"):
                st.write("计划调整建议：" + str(analysis.get("plan_adjustment_suggestion")))
            unconfirmed = analysis.get("unconfirmed") or []
            if unconfirmed:
                st.caption("仍无法确认：" + "；".join(str(item) for item in unconfirmed[:5]))
        sources = web_check.get("sources") or []
        if sources:
            with st.expander("查看来源"):
                render_source_links(sources, f"web-check-{owner_type}-{owner_id}")

    if st.button(button_label, key=f"web-check-start-{owner_type}-{owner_id}", disabled=bool(web_check and web_check.get("status") == "searching")):
        try:
            result = start_plan_web_check(owner_id) if owner_type == "plan" else start_task_web_check(owner_id)
            st.session_state[state_key] = result.get("web_check")
            st.success("联网核实已开始")
            st.rerun()
        except APIClientError:
            st.warning("联网搜索暂时失败，当前仍基于本地信息分析。")


def render_progress_plan(plan: dict, plan_key: str) -> None:
    status_label = {
        "active": "推进中",
        "paused": "已暂停",
        "completed": "已完成",
        "cancelled": "已结束",
        "failed": "失败",
    }.get(plan.get("system_status"), plan.get("system_status") or "推进中")
    st.markdown("### 推进计划")
    st.caption(f"状态：{status_label} | 版本：{plan.get('version_number') or 1} | 最近更新：{format_calendar_time(plan.get('updated_at'))}")
    st.markdown("**当前目标**")
    st.write(plan.get("goal") or "推进当前机会")
    st.markdown("**当前阶段**")
    st.write(plan.get("current_stage") or "正在推进")
    st.markdown("**当前计划**")
    st.write(plan.get("plan_summary") or "暂无计划说明")
    st.markdown("**当前任务列表**")
    items = plan.get("items") or []
    if items:
        for item in items:
            sync_text = "已同步待办" if item.get("synced_to_todo") else "未同步"
            st.write(
                "- **{title}**｜{priority}｜{status}｜{sync}\n  {desc}".format(
                    title=item.get("title") or "未命名任务",
                    priority=item.get("priority") or "medium",
                    status=item.get("status") or "pending",
                    sync=sync_text,
                    desc=item.get("description") or "",
                )
            )
    else:
        st.caption("暂无计划任务。")
    st.markdown("**下一步重点**")
    st.write(plan.get("next_focus") or "继续补齐关键信息。")
    risks = plan.get("risks") or []
    st.markdown("**风险与未确认信息**")
    if risks:
        for risk in risks:
            st.write(f"- {risk}")
    else:
        st.caption("暂无明确风险。")

    st.markdown("**信息来源**")
    web_key = f"web-check-plan-{plan.get('id')}"
    if web_key not in st.session_state:
        try:
            st.session_state[web_key] = get_latest_plan_web_check(int(plan["id"])).get("web_check")
        except APIClientError:
            st.session_state[web_key] = None
    render_web_check_result(st.session_state.get(web_key), "plan", int(plan["id"]), web_key)

    pending_update = plan.get("pending_update")
    if pending_update:
        render_progress_update(pending_update, int(plan["id"]), plan_key)
        return

    if plan.get("system_status") in {"completed", "cancelled"}:
        st.caption("该推进计划已结束，历史记录仍然保留。")
        return

    progress_key = f"progress-text-{plan.get('id')}"
    st.text_area(
        "告诉 AI 你最近完成了什么、遇到了什么问题，或计划发生了哪些变化。",
        key=progress_key,
        height=100,
        placeholder="例如：简历已经修改完成，但报名入口仍然无法确认。",
    )
    plan_cols = st.columns([1.5, 1.4, 1.2, 1.2, 1.2])
    if plan_cols[0].button("更新并重新规划", key=f"progress-replan-{plan.get('id')}"):
        try:
            with st.spinner("正在根据最新进展重新规划……"):
                result = replan_progress_plan(int(plan["id"]), st.session_state.get(progress_key, ""))
            st.session_state[plan_key] = result.get("plan")
            st.success(result.get("message") or "已生成调整建议")
            st.rerun()
        except APIClientError as exc:
            st.error(str(exc))

    selectable = [
        item
        for item in items
        if item.get("status") not in {"completed", "no_longer_applicable"} and not item.get("synced_to_todo")
    ]
    labels = {f"{item['id']}｜{item.get('title')}": item["id"] for item in selectable}
    selected_labels = st.multiselect("选择要同步到待办的计划任务", list(labels), key=f"progress-sync-select-{plan.get('id')}")
    if plan_cols[1].button("同步到待办", key=f"progress-sync-{plan.get('id')}"):
        try:
            result = sync_progress_items_to_todos(int(plan["id"]), [labels[label] for label in selected_labels])
            st.session_state[plan_key] = result.get("plan")
            sync_result = result.get("result") or {}
            st.success(
                "同步完成：新建 {created} 个，已存在 {existing} 个，失败 {failed} 个。".format(
                    created=len(sync_result.get("created") or []),
                    existing=len(sync_result.get("existing") or []),
                    failed=len(sync_result.get("failed") or []),
                )
            )
            st.rerun()
        except APIClientError as exc:
            st.error(str(exc))
    if plan_cols[2].button("标记为已完成", key=f"progress-complete-{plan.get('id')}"):
        try:
            result = set_progress_plan_status(int(plan["id"]), "complete")
            st.session_state[plan_key] = result.get("plan")
            st.success(result.get("message") or "已完成")
            st.rerun()
        except APIClientError as exc:
            st.error(str(exc))
    if plan_cols[3].button("暂停推进", key=f"progress-pause-{plan.get('id')}"):
        try:
            result = set_progress_plan_status(int(plan["id"]), "pause")
            st.session_state[plan_key] = result.get("plan")
            st.success(result.get("message") or "已暂停")
            st.rerun()
        except APIClientError as exc:
            st.error(str(exc))
    if plan_cols[4].button("结束推进", key=f"progress-close-{plan.get('id')}"):
        try:
            result = set_progress_plan_status(int(plan["id"]), "close")
            st.session_state[plan_key] = result.get("plan")
            st.success(result.get("message") or "已结束")
            st.rerun()
        except APIClientError as exc:
            st.error(str(exc))


def render_opportunity_progress_panel(user_id: int, opp_id: int, task_key: str, plan_key: str) -> None:
    plan = st.session_state.get(plan_key)
    if plan:
        with st.expander("推进计划", expanded=True):
            render_progress_plan(plan, plan_key)
        return

    task = st.session_state.get(task_key)
    if not task:
        return

    with st.expander("AI 推进当前机会", expanded=True):
        if task.get("status") == "analyzing":
            st.info("正在分析当前机会并规划下一步……")
            try:
                latest = get_opportunity_progress_task(int(task["id"]))
                st.session_state[task_key] = latest.get("task") or task
            except APIClientError as exc:
                st.warning(str(exc))
            time.sleep(2)
            st.rerun()
            return
        if task.get("status") == "failed":
            st.error(task.get("error_message") or "AI 推进分析失败，请稍后重试。")
            if st.button("重新分析", key=f"progress-retry-{task.get('id')}-{opp_id}"):
                try:
                    result = analyze_opportunity_progress(user_id, opp_id)
                    st.session_state[task_key] = result.get("task") or {}
                    st.success("AI 推进分析已重新开始")
                    st.rerun()
                except APIClientError as exc:
                    st.error(str(exc))
            return
        rounds = task.get("rounds") or []
        if rounds:
            st.markdown("**分析过程**")
            for item in rounds:
                title = item.get("stage_title") or "继续分析"
                summary = item.get("round_summary") or ""
                st.markdown(f"**{html_text(title)}**")
                if summary:
                    st.write(summary)
                findings = item.get("findings") or []
                uncertainties = item.get("uncertainties") or []
                if findings:
                    st.caption("已确认：" + "；".join(str(value) for value in findings[:4]))
                if uncertainties:
                    st.caption("待确认：" + "；".join(str(value) for value in uncertainties[:4]))

        final_result = task.get("final_result") or {}
        if final_result:
            st.markdown("**推进结论**")
            judgment = final_result.get("overall_judgment") or "建议观察"
            summary = final_result.get("summary") or ""
            st.info(f"{judgment}。{summary}")

            source_assessment = final_result.get("source_assessment") or "当前只能基于项目已有信息判断。"
            st.markdown("**信息源检查结果**")
            st.write(source_assessment)
            web_key = f"web-check-task-{task.get('id')}"
            if web_key not in st.session_state:
                try:
                    st.session_state[web_key] = get_latest_task_web_check(int(task["id"])).get("web_check")
                except APIClientError:
                    st.session_state[web_key] = None
            render_web_check_result(st.session_state.get(web_key), "task", int(task["id"]), web_key)

            st.markdown("**同类机会比较结果**")
            st.write(final_result.get("similar_opportunity_comparison") or "当前机会库中没有足够同类数据可比较。")

            st.markdown("**用户匹配程度**")
            st.write(final_result.get("user_fit") or "暂未形成明确匹配判断。")

            advantages = final_result.get("advantages") or []
            risks = final_result.get("risks_and_uncertainties") or []
            if advantages:
                st.markdown("**优势**")
                for value in advantages:
                    st.write(f"- {value}")
            if risks:
                st.markdown("**风险与不确定信息**")
                for value in risks:
                    st.write(f"- {value}")

            st.markdown("**推荐推进方案**")
            st.write(final_result.get("recommended_strategy") or "建议先补齐关键信息，再决定是否推进。")

            suggested_todos = final_result.get("suggested_todos") or task.get("suggested_todos") or []
            if suggested_todos:
                st.markdown("**建议加入的待办**")
                for item in suggested_todos:
                    if isinstance(item, dict):
                        title = item.get("title") or "推进当前机会"
                        description = item.get("description") or ""
                        st.write(f"- **{title}**：{description}")

            status = task.get("status") or ""
            should_create = bool(final_result.get("should_create_todos"))
            if status in {"waiting_confirmation", "stopped"}:
                confirm_cols = st.columns([1.5, 1.2, 4])
                if confirm_cols[0].button("建立推进计划", key=f"progress-create-plan-{task.get('id')}-{opp_id}"):
                    try:
                        result = create_progress_plan_from_task(int(task["id"]))
                        st.session_state[plan_key] = result.get("plan")
                        st.session_state.pop(task_key, None)
                        st.success(result.get("message") or "推进计划已建立。")
                        st.rerun()
                    except APIClientError as exc:
                        st.error(str(exc))
                if confirm_cols[1].button("暂不建立", key=f"progress-dismiss-{task.get('id')}-{opp_id}"):
                    st.session_state.pop(task_key, None)
                    st.rerun()
            elif status == "completed":
                st.success("已建立推进计划。")
            elif not should_create:
                st.caption("当前结论不建议强行创建待办。")


def render_opportunity_actions(
    user_id: int,
    opportunity: dict,
    recommendation: dict | None = None,
    context: str = "source_article_detail",
    article_context: dict | None = None,
) -> None:
    rec = recommendation or {}
    rec_id = rec.get("id") or f"opp-{opportunity['id']}"
    opp_id = opportunity["id"]
    state = rec.get("opportunity_state") or opportunity.get("opportunity_state") or ""
    calendar_created = state == "calendar_created"
    todo_only_created = state == "todo_created"
    todo_calendar_done = calendar_created or todo_only_created
    todo_calendar_label = "已加入日历" if calendar_created else ("已加入待办" if todo_only_created else "待办加入日历")
    progress_key = f"{context}-progress-task-{rec_id}-{opp_id}"
    plan_key = f"{context}-progress-plan-{rec_id}-{opp_id}"
    try:
        current_plan = get_current_progress_plan(user_id, opp_id).get("plan")
        if current_plan:
            st.session_state[plan_key] = current_plan
        elif not st.session_state.get(progress_key):
            latest_task = get_latest_opportunity_progress_task(user_id, opp_id).get("task")
            if latest_task and latest_task.get("status") in {"analyzing", "waiting_confirmation", "stopped", "failed"}:
                st.session_state[progress_key] = latest_task
    except APIClientError:
        current_plan = st.session_state.get(plan_key)

    action_cols = st.columns(3)
    if action_cols[0].button("问问机会助理", key=f"{context}-assistant-open-btn-{rec_id}-{opp_id}"):
        st.session_state[f"assistant-open-{rec_id}-{opp_id}"] = True
    progress_button_label = "查看推进计划" if current_plan or st.session_state.get(plan_key) else "让 AI 帮我推进"
    progress_busy_key = f"{context}-progress-busy-{rec_id}-{opp_id}"
    if action_cols[1].button(progress_button_label, key=f"{context}-progress-open-btn-{rec_id}-{opp_id}", disabled=st.session_state.get(progress_busy_key, False)):
        if current_plan or st.session_state.get(plan_key):
            st.session_state[f"progress-open-{rec_id}-{opp_id}"] = True
        else:
            try:
                st.session_state[progress_busy_key] = True
                with st.spinner("正在创建分析任务……"):
                    result = analyze_opportunity_progress(user_id, opp_id)
                st.session_state[progress_key] = result.get("task") or {}
                st.success("AI 推进分析已开始")
            except APIClientError as exc:
                st.error(str(exc))
            finally:
                st.session_state[progress_busy_key] = False
    if action_cols[2].button(todo_calendar_label, key=f"{context}-todo-calendar-{rec_id}-{opp_id}", disabled=todo_calendar_done):
        try:
            todo_result = create_todo_from_opportunity(user_id, opp_id)
            calendar_result = run_action(opp_id, user_id, "calendar")
            calendar_summary = calendar_result["result"].get("summary", "已创建提醒计划")
            if todo_result.get("already_exists"):
                st.success(f"待办已存在，{calendar_summary}")
            else:
                st.success(f"待办已创建，{calendar_summary}")
            st.rerun()
        except APIClientError as exc:
            st.error(str(exc))
    render_opportunity_assistant(user_id, rec or {"id": rec_id}, opportunity, article_context=article_context)
    if st.session_state.get(f"progress-open-{rec_id}-{opp_id}") or st.session_state.get(progress_key) or st.session_state.get(plan_key):
        render_opportunity_progress_panel(user_id, opp_id, progress_key, plan_key)

    feedback_cols = st.columns(3)
    feedback_open_key = f"{context}-feedback-open-{rec_id}-{opp_id}"
    feedback_text_key = f"{context}-feedback-text-{rec_id}-{opp_id}"
    if feedback_cols[0].button("有用", key=f"{context}-mark_useful-{rec_id}-{opp_id}", disabled=not rec.get("id")):
        st.session_state[feedback_open_key] = "mark_useful"
    if feedback_cols[1].button("不感兴趣", key=f"{context}-mark_not_interested-{rec_id}-{opp_id}", disabled=not rec.get("id")):
        st.session_state[feedback_open_key] = "mark_not_interested"
    applied = state == "applied"
    if feedback_cols[2].button("已申请", key=f"{context}-apply-{rec_id}-{opp_id}", disabled=applied):
        try:
            mark_opportunity_applied(user_id, opp_id)
            st.success("已标记为已申请")
            st.rerun()
        except APIClientError as exc:
            st.error(str(exc))

    active_feedback = st.session_state.get(feedback_open_key)
    if active_feedback in {"mark_useful", "mark_not_interested"} and rec.get("id"):
        feedback_label = "有用" if active_feedback == "mark_useful" else "不感兴趣"
        st.text_area(
            f"补充一句理由（可选）｜{feedback_label}",
            key=feedback_text_key,
            height=80,
            placeholder="例如：方向感兴趣，但希望更偏工程落地和长期实习。",
        )
        submit_cols = st.columns([1.2, 5])
        if submit_cols[0].button("提交反馈", key=f"{context}-submit-feedback-{rec_id}-{opp_id}"):
            try:
                send_recommendation_feedback(
                    user_id,
                    int(rec["id"]),
                    opp_id,
                    active_feedback,
                    st.session_state.get(feedback_text_key, ""),
                )
                st.session_state.pop(feedback_open_key, None)
                st.session_state.pop(feedback_text_key, None)
                st.success("已记录")
                st.rerun()
            except APIClientError:
                st.error("反馈提交失败，请稍后重试。")


def open_source_article_detail(user_id: int, rec: dict, opp: dict) -> None:
    st.session_state["current_page"] = "source_article_detail"
    st.session_state["selected_user_id"] = user_id
    st.session_state["selected_opportunity_id"] = opp["id"]
    st.session_state["selected_recommendation_id"] = rec["id"]
    st.session_state["selected_recommendation"] = rec
    st.session_state["selected_opportunity"] = opp
    st.rerun()


def render_recommendations_page() -> None:
    if st.session_state.get("current_page") == "recommendations":
        if st.button("返回主导航"):
            st.session_state.pop("current_page", None)
            st.rerun()
    st.subheader("生成推荐")
    try:
        profile_options = api_get("/profiles")
    except Exception:
        profile_options = []
    if profile_options:
        selected_rec_profile = st.selectbox(
            "选择用户画像",
            profile_options,
            format_func=lambda item: f"{item['id']} - {item['name']}（{item.get('grade_identity') or '未注明身份'}）",
            key="recommend_profile_select",
        )
        user_id = int(selected_rec_profile["id"])
    else:
        user_id = st.number_input("用户 ID", min_value=1, value=1, step=1)
    refresh_key = f"recommend_refreshing_{user_id}"
    if st.button("生成/刷新推荐", disabled=st.session_state.get(refresh_key, False)):
        try:
            st.session_state[refresh_key] = True
            api_post(f"/recommendations/generate/{user_id}?force=true", timeout=120)
            st.success("推荐刷新已开始，稍后会自动保留/更新列表")
        except APIClientError as exc:
            st.error(str(exc))
        finally:
            st.session_state[refresh_key] = False
    try:
        recommendations = api_get(f"/recommendations/{user_id}")
        for rec in recommendations:
            opp = rec["opportunity"]
            with st.container(border=True):
                st.markdown(f"<div class='rec-title'>{html_text(opp['name'])}</div>", unsafe_allow_html=True)
                st.markdown(
                    f"<div class='rec-meta'>当前状态：{html_text(recommendation_state_label(rec.get('opportunity_state')))} | {html_text(verification_badge(opp))}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"<div class='rec-meta'>截止时间：{html_text(rec.get('deadline') or opp['deadline'] or '未注明')}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown("<div class='rec-section'>内容概括</div>", unsafe_allow_html=True)
                overview = clean_overview_text(rec.get("content_overview") or opp.get("summary") or opp["name"], opp["name"])
                render_long_text(overview, key=f"overview-{rec['id']}-{opp['id']}")
                st.markdown("<div class='rec-section'>相关性</div>", unsafe_allow_html=True)
                st.markdown(
                    f"<div class='rec-body'>{html_text(rec.get('relevance_explanation') or rec['reason'])}</div>",
                    unsafe_allow_html=True,
                )
                if st.button("查看原文", key=f"view-source-{rec['id']}-{opp['id']}"):
                    open_source_article_detail(user_id, rec, opp)
    except Exception as exc:
        st.info(f"暂无推荐。{exc}")


def render_source_article_detail() -> None:
    user_id = st.session_state.get("selected_user_id")
    opportunity_id = st.session_state.get("selected_opportunity_id")
    recommendation = st.session_state.get("selected_recommendation") or {}
    opportunity = st.session_state.get("selected_opportunity") or recommendation.get("opportunity") or {}
    if not user_id or not opportunity_id:
        st.session_state["current_page"] = "recommendations"
        st.warning("未选择机会，请返回推荐排序。")
        if st.button("返回推荐排序"):
            st.session_state["current_page"] = "recommendations"
            st.rerun()
        return

    if st.button("返回推荐排序"):
        st.session_state["current_page"] = "recommendations"
        st.rerun()

    st.header("查看原文")
    try:
        source_article = get_source_article(int(opportunity_id))
    except Exception:
        source_article = {
            "opportunity_id": opportunity_id,
            "opportunity_name": opportunity.get("name") or "未命名机会",
            "article_title": opportunity.get("name") or "暂未找到完整原文",
            "source": "机会摘要",
            "published_at": "",
            "original_url": opportunity.get("link") or opportunity.get("official_url") or "",
            "content": "\n\n".join(
                item
                for item in [
                    "暂未找到完整原文，可先查看机会摘要。",
                    opportunity.get("summary") or "",
                    opportunity.get("requirements") or "",
                    opportunity.get("deadline") or "",
                ]
                if item
            ),
            "fallback_used": True,
        }

    opportunity_name = source_article.get("opportunity_name") or opportunity.get("name") or "未命名机会"
    st.markdown(f"<div class='rec-title'>{html_text(opportunity_name)}</div>", unsafe_allow_html=True)
    st.caption(
        "文章标题：{title} | 来源：{source} | 发布时间：{published}".format(
            title=source_article.get("article_title") or "未注明",
            source=source_article.get("source") or "未注明",
            published=source_article.get("published_at") or "未注明",
        )
    )
    if source_article.get("original_url"):
        st.markdown(f"[打开原文链接]({source_article['original_url']})")
    if source_article.get("fallback_used"):
        st.info("暂未找到完整原文，可先查看机会摘要。")

    content = source_article.get("content") or opportunity.get("summary") or "暂无原文内容。"
    escaped_content = html_text(content).replace("\n", "<br>")
    st.markdown(
        f"""
        <div style="
            max-height: 540px;
            overflow-y: auto;
            border: 1px solid #e5e7eb;
            border-radius: 8px;
            padding: 16px;
            line-height: 1.72;
            background: #ffffff;
            white-space: normal;
        ">{escaped_content}</div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("### 行动")
    action_opportunity = dict(opportunity)
    action_opportunity.setdefault("id", int(opportunity_id))
    action_opportunity.setdefault("name", opportunity_name)
    action_opportunity.setdefault("summary", source_article.get("content") or "")
    render_opportunity_actions(
        int(user_id),
        action_opportunity,
        recommendation=recommendation,
        context="source_article_detail",
        article_context=source_article,
    )


with st.sidebar:
    st.header("演示控制台")
    st.caption("先启动 FastAPI：uvicorn app.main:app --reload")
    sample_path = str(Path(__file__).resolve().parents[1] / "data" / "wechat_articles.json")
    import_path = st.text_input("导入文件路径", value=sample_path)
    if st.button("导入文章", use_container_width=True):
        result = api_post("/articles/import", {"path": import_path})
        st.success(f"已导入 {result['imported']} 篇文章")
    if st.button("处理未抽取文章", use_container_width=True):
        result = api_post("/pipeline/process")
        st.success(f"处理 {result['processed_articles']} 篇文章，新增 {result['created_opportunities']} 个机会")


if st.session_state.get("current_page") == "source_article_detail":
    render_source_article_detail()
    st.stop()
if st.session_state.get("current_page") == "recommendations":
    render_recommendations_page()
    st.stop()


tab_profile, tab_articles, tab_opps, tab_recs, tab_memory, tab_calendar = st.tabs(
    ["用户画像", "文章库", "机会库", "推荐排序", "成长记忆", "日历"]
)


with tab_profile:
    st.subheader("创建用户画像")
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("姓名", "演示用户", key="create_name")
        major_direction = st.text_input("专业方向", "人工智能", key="create_major_direction")
        grade_identity = st.text_input("年级/身份", "大三本科生", key="create_grade_identity")
        current_goals_text = st.text_input("当前目标关键词，用逗号分隔", "科研入门, 找实习, 保研", key="create_goals")
    with col2:
        skills = st.text_input("技能栈，用逗号分隔", "Python, 机器学习, PyTorch, SQL", key="create_skills")
        interested_fields = st.text_input("感兴趣领域，用逗号分隔", "AI Agent, 多模态, 数据分析, RAG", key="create_interests")
        disliked_contents = st.text_input("不感兴趣内容，用逗号分隔", "纯营销, 无证书训练营", key="create_dislikes")
        time_preference = st.text_input("时间偏好", "暑期 周末 晚上", key="create_time_preference")
        location_preference = st.text_input("地点偏好", "线上 北京 上海 深圳", key="create_location_preference")
    detailed_needs = st.text_area(
        "更具体的需求描述",
        "希望找到对保研和科研经历有帮助的 AI Agent / RAG 相关机会，最好能产出项目、论文复现或导师推荐信；实习方向偏数据分析和机器学习，地点优先线上、北京、上海、深圳。",
        height=110,
        key="create_detailed_needs",
    )
    if st.button("保存画像"):
        payload = {
            "name": name,
            "major_direction": major_direction,
            "grade_identity": grade_identity,
            "current_goals": split_csv(current_goals_text),
            "skills": split_csv(skills),
            "interested_fields": split_csv(interested_fields),
            "disliked_contents": split_csv(disliked_contents),
            "detailed_needs": detailed_needs,
            "time_preference": time_preference,
            "location_preference": location_preference,
        }
        created = api_post("/profiles", payload)
        st.success(f"画像已保存，用户 ID：{created['id']}")

    st.subheader("管理已有画像")
    try:
        profiles = api_get("/profiles")
        if not profiles:
            st.info("还没有已创建的画像。")
        else:
            selected_profile = st.selectbox(
                "选择要查看或修改的画像",
                profiles,
                format_func=lambda item: f"{item['id']} - {item['name']}（{item.get('grade_identity') or '未注明身份'}）",
            )
            st.dataframe(
                [
                    {
                        "ID": item["id"],
                        "姓名": item["name"],
                        "专业方向": item["major_direction"],
                        "身份": item["grade_identity"],
                        "当前目标": join_csv(item["current_goals"]),
                        "兴趣": join_csv(item["interested_fields"]),
                        "更新时间": item["updated_at"],
                    }
                    for item in profiles
                ],
                use_container_width=True,
            )
            with st.form(f"profile-edit-{selected_profile['id']}"):
                edit_col1, edit_col2 = st.columns(2)
                with edit_col1:
                    edit_name = st.text_input("姓名", selected_profile["name"])
                    edit_major = st.text_input("专业方向", selected_profile["major_direction"])
                    edit_grade = st.text_input("年级/身份", selected_profile["grade_identity"])
                    edit_goals = st.text_input("当前目标关键词，用逗号分隔", join_csv(selected_profile["current_goals"]))
                with edit_col2:
                    edit_skills = st.text_input("技能栈，用逗号分隔", join_csv(selected_profile["skills"]))
                    edit_interests = st.text_input("感兴趣领域，用逗号分隔", join_csv(selected_profile["interested_fields"]))
                    edit_dislikes = st.text_input("不感兴趣内容，用逗号分隔", join_csv(selected_profile["disliked_contents"]))
                    edit_time = st.text_input("时间偏好", selected_profile["time_preference"])
                    edit_location = st.text_input("地点偏好", selected_profile["location_preference"])
                edit_needs = st.text_area("更具体的需求描述", selected_profile["detailed_needs"], height=130)
                if st.form_submit_button("保存修改"):
                    updated = api_put(
                        f"/profiles/{selected_profile['id']}",
                        {
                            "name": edit_name,
                            "major_direction": edit_major,
                            "grade_identity": edit_grade,
                            "current_goals": split_csv(edit_goals),
                            "skills": split_csv(edit_skills),
                            "interested_fields": split_csv(edit_interests),
                            "disliked_contents": split_csv(edit_dislikes),
                            "detailed_needs": edit_needs,
                            "time_preference": edit_time,
                            "location_preference": edit_location,
                        },
                    )
                    st.success(f"已更新画像：{updated['name']}")
                    st.rerun()
            st.warning("删除画像会同时删除该用户的推荐、状态、反馈事件、摘要和工具日志。")
            confirm_delete = st.checkbox(
                f"确认删除用户 {selected_profile['id']} - {selected_profile['name']}",
                key=f"confirm-delete-profile-{selected_profile['id']}",
            )
            if st.button("删除该画像", disabled=not confirm_delete, type="secondary"):
                api_delete(f"/profiles/{selected_profile['id']}")
                st.success("画像已删除")
                st.rerun()
    except Exception as exc:
            st.info(f"请先启动后端服务。{exc}")


with tab_articles:
    st.subheader("文章库")
    st.caption("运行时文章数据只从 SQLite 后端读取。文件系统仅用于采集、入库和调试。")
    try:
        articles = api_get("/articles")
        if not articles:
            st.info("还没有文章。可以先导入 JSON，或通过采集脚本推送 clean article。")
        else:
            st.write(f"当前共有 {len(articles)} 篇文章")
            for article in articles:
                article_id = int(article["id"])
                with st.container(border=True):
                    title_col, action_col = st.columns([5, 1.4])
                    with title_col:
                        st.markdown(f"**{article.get('title') or '未命名文章'}**")
                        st.caption(
                            "ID: {id} | 来源: {source} | processed={processed} | imported_at={imported_at}".format(
                                id=article_id,
                                source=article.get("source") or "",
                                processed=article.get("processed"),
                                imported_at=article.get("imported_at") or "",
                            )
                        )
                        content = article.get("content") or ""
                        preview = content[:260] + ("..." if len(content) > 260 else "")
                        st.text(preview)
                    with action_col:
                        confirm_key = f"confirm-delete-article-{article_id}"
                        confirmed = st.checkbox("确认删除", key=confirm_key)
                        if st.button("删除文章", key=f"delete-article-{article_id}", disabled=not confirmed, type="secondary"):
                            result = api_delete(f"/articles/{article_id}")
                            st.success(
                                "已删除文章 {article_id}，关联机会 {opps} 个，推荐 {recs} 条".format(
                                    article_id=article_id,
                                    opps=result.get("deleted_opportunities", 0),
                                    recs=result.get("deleted_recommendations", 0),
                                )
                            )
                            st.rerun()
    except Exception as exc:
        st.info(f"暂无文章或后端未启动。{exc}")


with tab_opps:
    st.subheader("机会库")
    try:
        opportunities = api_get("/opportunities")
        st.dataframe(
            [
                {
                    "ID": item["id"],
                    "名称": item["name"],
                    "类型": item["category"],
                    "截止": item["deadline"],
                    "地点": item["location"],
                    "主办方": item["organizer"],
                    "摘要": item["summary"],
                }
                for item in opportunities
            ],
            use_container_width=True,
        )
    except Exception as exc:
        st.info(f"暂无机会或后端未启动。{exc}")


with tab_recs:
    render_recommendations_page()


with tab_calendar:
    st.subheader("日历")
    try:
        profile_options = api_get("/profiles")
    except Exception:
        profile_options = []
    if profile_options:
        selected_calendar_profile = st.selectbox(
            "选择用户画像",
            profile_options,
            format_func=lambda item: f"{item['id']} - {item['name']}（{item.get('grade_identity') or '未注明身份'}）",
            key="calendar_profile_select",
        )
        calendar_user_id = int(selected_calendar_profile["id"])
    else:
        calendar_user_id = st.number_input("日历用户 ID", min_value=1, value=1, step=1, key="calendar_user_id")

    try:
        dashboard = get_calendar_dashboard(calendar_user_id)
        upcoming = sorted(dashboard.get("upcoming_7_days") or [], key=calendar_sort_key)
        later = sorted(dashboard.get("later_events") or [], key=calendar_sort_key)
        expired = sorted(dashboard.get("expired_events") or [], key=calendar_sort_key)
        uncertain = dashboard.get("uncertain_deadline_todos") or []

        st.markdown("### 近期提醒")
        if upcoming:
            render_calendar_grouped(upcoming, "upcoming")
        else:
            st.caption("未来 7 天暂无明确日期提醒。")

        st.markdown("### 更晚提醒")
        if later:
            render_calendar_grouped(later, "later")
        else:
            st.caption("暂无更晚的明确日期提醒。")

        st.markdown("### 已加入待办，日期待确认")
        st.caption("以下机会已生成待办，但原文缺少明确日期，暂未加入日历提醒。")
        if uncertain:
            for todo in uncertain:
                title = todo.get("short_title") or str(todo.get("title") or "待确认机会").replace("准备并申请：", "")
                reason = todo.get("date_uncertain_reason") or todo.get("deadline_note") or "原文未注明明确日期"
                st.write(f"- {title} ｜ {reason}")
        else:
            st.caption("暂无日期待确认的待办。")

        if expired:
            with st.expander("已过期提醒"):
                render_calendar_grouped(expired, "expired")
    except APIClientError as exc:
        st.info(str(exc))
    except Exception as exc:
        st.info(f"暂无日历数据。{exc}")


with tab_memory:
    st.subheader("成长记忆")
    st.caption("综合你的用户画像与近期行为，分析你当前所处的阶段、关注重点和下一步方向。")
    memory_user_id = st.number_input("当前演示用户", min_value=1, value=1, step=1, key="memory_user_id")
    try:
        latest_response = get_latest_growth_memory(memory_user_id)
        latest_memory = latest_response.get("data")
    except Exception:
        latest_memory = None

    button_label = "刷新成长记忆" if latest_memory else "生成成长记忆"
    if st.button(button_label, key="generate_growth_memory"):
        try:
            with st.spinner("正在分析你的近期行为并生成成长记忆……"):
                result = generate_growth_memory(memory_user_id)
            message = result.get("message") or ("成长记忆已更新。" if result.get("updated") else "")
            if message:
                st.success(message) if result.get("updated") else st.info(message)
            latest_memory = result.get("data") or latest_memory
        except APIClientError as exc:
            detail = str(exc)
            if "没有足够的行为记录" in detail:
                st.info("目前还没有足够的行为记录，暂时无法生成成长记忆。")
            else:
                st.error("成长记忆生成失败，请稍后重试。")
        except Exception:
            st.error("成长记忆生成失败，请稍后重试。")

    if latest_memory:
        sections = [
            ("当前阶段", latest_memory.get("current_stage")),
            ("近期关注重点", latest_memory.get("recent_focus")),
            ("判断标准与偏好变化", latest_memory.get("preference_changes")),
            ("整体观察", latest_memory.get("overall_observation")),
            ("下一阶段建议", latest_memory.get("next_stage_advice")),
        ]
        for title, body in sections:
            with st.container(border=True):
                st.markdown(f"**{title}**")
                st.write(body or "暂无内容")
        behavior_count = latest_memory.get("behavior_count") or 0
        created_at = str(latest_memory.get("created_at") or "")[:16].replace("T", " ")
        st.caption(f"基于最近 {behavior_count} 条行为生成 · 更新于 {created_at}")
    else:
        st.info("目前还没有成长记忆。点击上方按钮后，将根据你的用户画像和近期行为生成。")
