# 设计说明

## MVP 边界

- 不处理公众号登录、反爬和真实抓取。
- 文章来源为本地 JSON / Markdown / TXT。
- 大模型抽取、总结可接真实 API；没有 Key 时使用规则 fallback。
- 工具调用先 mock，保留接口和调用记录。

## 数据库表

### user_profiles

保存用户画像。

- id
- name
- major_direction
- grade_identity
- current_goals
- skills
- interested_fields
- disliked_contents
- time_preference
- location_preference
- created_at
- updated_at

### articles

保存导入的公众号文章。

- id
- title
- source
- author
- published_at
- content
- url
- imported_at
- processed

### opportunities

保存从文章中抽取的机会。

- id
- article_id
- name
- category
- organizer
- event_time
- deadline
- location
- target_audience
- link
- requirements
- summary
- benefit
- cost
- credibility
- raw_payload
- created_at

### recommendations

保存用户和机会之间的推荐结果。

- id
- user_id
- opportunity_id
- relevance_score
- urgency_score
- benefit_score
- cost_score
- credibility_score
- total_score
- reason
- action_suggestion
- created_at

### summaries

保存每天/每周摘要。

- id
- user_id
- period
- content
- created_at

### tool_calls

保存 mock 工具调用记录。

- id
- user_id
- opportunity_id
- tool_name
- payload
- status
- result
- created_at

## 核心模块

- `import_service`：解析本地文件并写入文章表。
- `llm_service`：封装真实大模型和 mock fallback。
- `extraction_service`：从文章抽取结构化机会。
- `matching_service`：根据用户画像打分、排序、生成行动建议。
- `summary_service`：生成摘要，供定时任务或手动接口调用。
- `scheduler`：APScheduler 定时触发摘要任务。
- `tool_service`：搜索、日历、邮件、待办 mock。

## 打分逻辑

总分 100：

- 相关性 40：目标、专业、技能、兴趣匹配；不感兴趣内容扣分。
- 紧急程度 20：deadline 越近越高，已过期为低分。
- 收益 20：科研/实习/保研/竞赛等按用户目标加权。
- 成本 10：地点、时间、要求复杂度越低越高。
- 可信度 10：主办方清晰、链接存在、来源可信度。

## API

- `POST /profiles`
- `GET /profiles`
- `POST /articles/import`
- `GET /articles`
- `POST /pipeline/process`
- `GET /opportunities`
- `POST /recommendations/generate/{user_id}`
- `GET /recommendations/{user_id}`
- `POST /summaries/generate/{user_id}`
- `GET /summaries/{user_id}`
- `POST /tools/{tool_name}`
