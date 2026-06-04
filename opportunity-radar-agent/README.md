# 个性化公众号机会雷达 Agent

一个可演示 MVP：导入本地公众号文章文本，抽取竞赛/实习/科研/讲座等机会，根据用户画像打分排序，并定时生成摘要。

## 核心流程

1. 维护用户画像：专业、年级、目标、技能、兴趣、排除项、时间/地点偏好。
2. 导入文章：支持 JSON、Markdown、TXT。
3. 信息抽取：优先调用大模型 API；没有 API Key 时使用规则 fallback，保证演示可跑。
4. 机会入库：活动名称、类型、主办方、时间、截止日期、地点、适合人群、链接、要求等。
5. 个性化推荐：综合相关性、紧急程度、收益、成本、可信度打分。
6. 定时摘要：APScheduler 每天/每周触发推荐生成。
7. 行动建议：为高价值机会生成下一步建议。
8. 工具预留：搜索、日历、邮件草稿、待办事项接口目前为 mock。

## 项目结构

```text
opportunity-radar-agent/
  app/
    api/              FastAPI 路由
    core/             配置、数据库、调度器
    models/           SQLAlchemy 表模型
    schemas/          Pydantic DTO
    services/         导入、抽取、匹配、摘要、mock 工具
    main.py           FastAPI 入口
  frontend/
    streamlit_app.py  简单演示前端
  data/
    sample_articles.json
  docs/
    design.md         模块划分、表设计、核心流程
  requirements.txt
  .env.example
```

## 快速启动

```powershell
cd opportunity-radar-agent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
python -m app.seed
uvicorn app.main:app --reload
```

另开一个终端启动前端：

```powershell
cd opportunity-radar-agent
.\.venv\Scripts\Activate.ps1
streamlit run frontend/streamlit_app.py
```

API 文档地址：

- http://127.0.0.1:8000/docs

## 大模型配置

默认可以无 Key 演示。若要使用真实大模型，编辑 `.env`：

```text
LLM_PROVIDER=openai
OPENAI_API_KEY=你的_key
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

也支持 OpenAI-compatible 服务，只需要修改 `OPENAI_BASE_URL` 和 `OPENAI_MODEL`。
