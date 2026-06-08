from __future__ import annotations

import json
from html import escape
from pathlib import Path

import requests
import streamlit as st


API_BASE = "http://127.0.0.1:8000"


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
    "email_drafted": "已生成邮件",
    "applied": "已申请",
    "archived": "已归档",
}


def state_label(state: str) -> str:
    return STATE_LABELS.get(state or "new", state or "新机会")


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


def api_get(path: str):
    response = requests.get(f"{API_BASE}{path}", timeout=20)
    response.raise_for_status()
    return response.json()


def api_post(path: str, payload: dict | None = None):
    response = requests.post(f"{API_BASE}{path}", json=payload or {}, timeout=60)
    response.raise_for_status()
    return response.json()


def api_put(path: str, payload: dict):
    response = requests.put(f"{API_BASE}{path}", json=payload, timeout=60)
    response.raise_for_status()
    return response.json()


def api_delete(path: str):
    response = requests.delete(f"{API_BASE}{path}", timeout=20)
    response.raise_for_status()
    return response.json()


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


tab_profile, tab_opps, tab_recs, tab_memory, tab_summary, tab_tools = st.tabs(
    ["用户画像", "机会库", "推荐排序", "成长记忆", "摘要反馈", "Mock 工具"]
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
    if st.button("生成/刷新推荐"):
        api_post(f"/recommendations/generate/{user_id}")
        st.success("推荐已刷新")
    try:
        recommendations = api_get(f"/recommendations/{user_id}")
        for rec in recommendations:
            opp = rec["opportunity"]
            with st.container(border=True):
                st.markdown(f"<div class='rec-title'>{html_text(opp['name'])}</div>", unsafe_allow_html=True)
                st.markdown(
                    f"<div class='rec-meta'>当前状态：{html_text(state_label(rec.get('opportunity_state')))} | {html_text(verification_badge(opp))}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(
                    f"<div class='rec-meta'>截止时间：{html_text(rec.get('deadline') or opp['deadline'] or '未注明')}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown("<div class='rec-section'>内容概括</div>", unsafe_allow_html=True)
                st.markdown(
                    f"<div class='rec-body'>{html_text(rec.get('content_overview') or opp.get('summary') or opp['name'])}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown("<div class='rec-section'>相关性</div>", unsafe_allow_html=True)
                st.markdown(
                    f"<div class='rec-body'>{html_text(rec.get('relevance_explanation') or rec['reason'])}</div>",
                    unsafe_allow_html=True,
                )
                st.markdown(f"**行动建议：** {rec['action_suggestion']}")
                st.caption(f"地点：{opp['location'] or '未注明'} | 链接：{opp['link'] or '无'}")
                with st.expander("进一步查询", expanded=False):
                    action_cols = st.columns(5)
                    if action_cols[0].button("核验真实性", key=f"verify-{opp['id']}"):
                        result = run_action(opp["id"], user_id, "verify")
                        st.success(result["result"].get("verification_summary", "核验完成"))
                        st.json(result["result"])
                        st.rerun()
                    if action_cols[1].button("深挖详情", key=f"enrich-{opp['id']}"):
                        result = run_action(opp["id"], user_id, "enrich")
                        st.success(result["result"].get("enrichment_summary", "补全完成"))
                        st.json(result["result"])
                        st.rerun()
                    if action_cols[2].button("加入日历", key=f"calendar-{opp['id']}"):
                        result = run_action(opp["id"], user_id, "calendar")
                        st.success(result["result"].get("summary", "已创建 mock 提醒"))
                        st.json(result["result"])
                        st.rerun()
                    if action_cols[3].button("生成邮件", key=f"email-{opp['id']}"):
                        result = run_action(opp["id"], user_id, "email")
                        st.success("已生成邮件草稿")
                        st.text_area("邮件草稿", result["result"].get("body", ""), height=220, key=f"email-body-{opp['id']}")
                        st.rerun()
                    if action_cols[4].button("生成待办", key=f"todo-{opp['id']}"):
                        result = run_action(opp["id"], user_id, "todo")
                        st.success("已生成待办")
                        for item in result["result"].get("todo_items", []):
                            st.write(f"- {item}")
                        st.rerun()
                    feedback_cols = st.columns(6)
                    feedback_map = [
                        ("有用", "mark_useful"),
                        ("不相关", "mark_irrelevant"),
                        ("不感兴趣", "mark_not_interested"),
                        ("太简单", "mark_too_easy"),
                        ("太难", "mark_too_hard"),
                        ("已申请", "apply_opportunity"),
                    ]
                    for label, event_type in feedback_map:
                        if feedback_cols[feedback_map.index((label, event_type))].button(label, key=f"{event_type}-{opp['id']}"):
                            log_memory_event(
                                user_id,
                                event_type,
                                opp["id"],
                                {"title": opp["name"], "category": opp["category"], "organizer": opp["organizer"]},
                            )
                            st.success(f"已记录反馈：{label}")
                            if event_type == "apply_opportunity":
                                st.rerun()
    except Exception as exc:
        st.info(f"暂无推荐。{exc}")


with tab_memory:
    st.subheader("Growth Memory")
    memory_user_id = st.number_input("记忆用户 ID", min_value=1, value=1, step=1, key="memory_user_id")
    col_a, col_b = st.columns(2)
    if col_a.button("生成/刷新 Reflection"):
        reflections = api_post("/memory/reflections/generate", {"user_id": memory_user_id})
        st.success(f"已生成 {len(reflections)} 条 reflection")
    if col_b.button("查看近期事件"):
        events = api_get(f"/memory/events/{memory_user_id}?limit=30")
        st.dataframe(events, use_container_width=True)
    try:
        reflections = api_get(f"/memory/reflections/{memory_user_id}")
        if reflections:
            for item in reflections:
                with st.container(border=True):
                    st.caption(f"{item['reflection_type']} | confidence {item['confidence']}")
                    st.write(item["summary"])
        else:
            st.info("还没有 reflection。先在推荐卡片上点击反馈或工具按钮，再生成 Reflection。")
    except Exception as exc:
        st.info(f"暂无记忆数据。{exc}")


with tab_summary:
    st.subheader("定时摘要反馈")
    summary_user_id = st.number_input("摘要用户 ID", min_value=1, value=1, step=1, key="summary_user_id")
    period = st.selectbox("周期", ["daily", "weekly"])
    if st.button("手动生成摘要"):
        summary = api_post(f"/summaries/generate/{summary_user_id}?period={period}")
        st.markdown(summary["content"])
    try:
        summaries = api_get(f"/summaries/{summary_user_id}")
        for item in summaries[:5]:
            with st.expander(f"{item['period']} | {item['created_at']}"):
                st.markdown(item["content"])
    except Exception as exc:
        st.info(f"暂无摘要。{exc}")


with tab_tools:
    st.subheader("预留工具调用")
    tool = st.selectbox("工具", ["search", "calendar", "email_draft", "todo"])
    tool_user_id = st.number_input("工具用户 ID", min_value=1, value=1, step=1)
    opportunity_id = st.number_input("机会 ID", min_value=1, value=1, step=1)
    payload_text = st.text_area("Payload JSON", value='{"note": "演示调用"}')
    if st.button("调用 Mock 工具"):
        payload = json.loads(payload_text)
        result = api_post(
            f"/tools/{tool}",
            {"user_id": tool_user_id, "opportunity_id": opportunity_id, "payload": payload},
        )
        st.json(result)
