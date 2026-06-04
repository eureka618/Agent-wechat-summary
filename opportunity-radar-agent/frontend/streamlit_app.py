from __future__ import annotations

import json
from pathlib import Path

import requests
import streamlit as st


API_BASE = "http://127.0.0.1:8000"


st.set_page_config(page_title="机会雷达 Agent", layout="wide")

st.title("个性化公众号机会雷达 Agent")


def api_get(path: str):
    response = requests.get(f"{API_BASE}{path}", timeout=20)
    response.raise_for_status()
    return response.json()


def api_post(path: str, payload: dict | None = None):
    response = requests.post(f"{API_BASE}{path}", json=payload or {}, timeout=60)
    response.raise_for_status()
    return response.json()


with st.sidebar:
    st.header("演示控制台")
    st.caption("先启动 FastAPI：uvicorn app.main:app --reload")
    sample_path = str(Path(__file__).resolve().parents[1] / "data" / "sample_articles.json")
    import_path = st.text_input("导入文件路径", value=sample_path)
    if st.button("导入文章", use_container_width=True):
        result = api_post("/articles/import", {"path": import_path})
        st.success(f"已导入 {result['imported']} 篇文章")
    if st.button("处理未抽取文章", use_container_width=True):
        result = api_post("/pipeline/process")
        st.success(f"处理 {result['processed_articles']} 篇文章，新增 {result['created_opportunities']} 个机会")


tab_profile, tab_opps, tab_recs, tab_summary, tab_tools = st.tabs(
    ["用户画像", "机会库", "推荐排序", "摘要反馈", "Mock 工具"]
)


with tab_profile:
    st.subheader("创建用户画像")
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("姓名", "演示用户")
        major_direction = st.text_input("专业方向", "人工智能")
        grade_identity = st.text_input("年级/身份", "大三本科生")
        current_goals = st.multiselect("当前目标", ["找实习", "科研入门", "竞赛加分", "保研", "申请项目"], ["科研入门", "找实习", "保研"])
    with col2:
        skills = st.text_input("技能栈，用逗号分隔", "Python, 机器学习, PyTorch, SQL")
        interested_fields = st.text_input("感兴趣领域，用逗号分隔", "AI Agent, 多模态, 数据分析, RAG")
        disliked_contents = st.text_input("不感兴趣内容，用逗号分隔", "纯营销, 无证书训练营")
        time_preference = st.text_input("时间偏好", "暑期 周末 晚上")
        location_preference = st.text_input("地点偏好", "线上 北京 上海 深圳")
    if st.button("保存画像"):
        payload = {
            "name": name,
            "major_direction": major_direction,
            "grade_identity": grade_identity,
            "current_goals": current_goals,
            "skills": [item.strip() for item in skills.split(",") if item.strip()],
            "interested_fields": [item.strip() for item in interested_fields.split(",") if item.strip()],
            "disliked_contents": [item.strip() for item in disliked_contents.split(",") if item.strip()],
            "time_preference": time_preference,
            "location_preference": location_preference,
        }
        created = api_post("/profiles", payload)
        st.success(f"画像已保存，用户 ID：{created['id']}")

    st.subheader("已有画像")
    try:
        profiles = api_get("/profiles")
        st.dataframe(profiles, use_container_width=True)
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
    user_id = st.number_input("用户 ID", min_value=1, value=1, step=1)
    if st.button("生成/刷新推荐"):
        api_post(f"/recommendations/generate/{user_id}")
        st.success("推荐已刷新")
    try:
        recommendations = api_get(f"/recommendations/{user_id}")
        for rec in recommendations:
            opp = rec["opportunity"]
            with st.container(border=True):
                top = st.columns([4, 1, 1])
                top[0].markdown(f"### {opp['name']}")
                top[1].metric("总分", rec["total_score"])
                top[2].write(opp["category"])
                st.write(rec["reason"])
                st.write(f"行动建议：{rec['action_suggestion']}")
                st.caption(f"截止：{opp['deadline'] or '未注明'} | 地点：{opp['location'] or '未注明'} | 链接：{opp['link'] or '无'}")
    except Exception as exc:
        st.info(f"暂无推荐。{exc}")


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
