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
- detailed_needs
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
- official_url
- registration_url
- verification_status
- credibility_score
- risk_level
- risk_flags
- verification_summary
- evidence_sources
- enriched_at
- verified_at
- created_at

增强字段只会在用户主动触发工具后更新。默认推荐流水线不会强制二次检索，也不会依赖这些字段。

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
- content_overview
- relevance_explanation
- risk_notes
- anti_recommendation_reason
- action_suggestion
- opportunity_state
- deadline
- deadline_urgency
- deadline_note
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

### tool_call_logs

保存按需 Agent 工具调用记录。

- id
- user_id
- opportunity_id
- action
- tool_name
- input_json
- output_json
- status
- created_at

### user_events

保存用户关键行为日志，只记录结构化事件，不保存聊天历史。

- id
- user_id
- event_type
- event_target
- event_metadata
- created_at

支持事件类型：

- view_opportunity
- save_opportunity
- verify_opportunity
- draft_email
- create_todo
- create_calendar
- mark_useful
- mark_irrelevant
- mark_not_interested
- mark_too_easy
- mark_too_hard
- apply_opportunity

### memory_reflections

保存由行为日志压缩得到的长期洞察。

- id
- user_id
- reflection_type
- summary
- confidence
- created_at
- updated_at

## 核心模块

- `import_service`：解析本地文件并写入文章表。
- `llm_service`：封装真实大模型和 mock fallback。
- `extraction_service`：从文章抽取结构化机会。
- `matching_service`：根据用户画像打分、排序、生成行动建议。
- `summary_service`：生成摘要，供定时任务或手动接口调用。
- `scheduler`：APScheduler 定时触发摘要任务。
- `tool_service`：搜索、日历、邮件、待办 mock。
- `agent_tools`：Pydantic AI 风格轻量工具层，包含 `BaseTool`、`ToolRegistry`、mock search、核验、补全、日历、邮件、待办。
- `opportunity_agent`：按用户 action 选择工具，执行后写入数据库。结构预留为 `OpportunityState -> ToolDecision -> ToolExecution -> ResultPersist -> UserFeedback`。
- `growth_memory.memory_updater`：写入关键行为日志。
- `growth_memory.reflection_generator`：基于最近 30 天或最近 100 条行为生成压缩 reflection。
- `growth_memory.memory_retriever`：根据当前 opportunity 类型按需取回相关 reflection。
- `growth_memory.memory_manager`：统一管理事件、reflection 生成和检索。

## 打分逻辑

总分是内部排序信号，前端不直接展示具体分数。当前权重：

```text
total =
  relevance * 0.55
+ urgency * 0.05
+ benefit * 0.35
+ cost * 0.05
+ 状态/核验/截止时间修正
+ 地点/时间偏好修正
```

- 相关性 `relevance`：先由规则根据目标、专业、技能、兴趣、详细需求关键词匹配得到基础分；配置 DeepSeek/OpenAI 时，会再调用模型根据文章语义和用户画像校正。
- 紧急程度 `urgency`：deadline 越近越高，已过期为低分。
- 成长收益 `benefit`：先使用抽取阶段的机会价值估计；配置 DeepSeek/OpenAI 时，会再调用模型根据成长、申请、简历、科研价值校正。
- 准备轻重 `cost`：要求越轻、行动成本越低，得分越高。
- 可信度 `credibility`：仍会保存并用于核验展示/风险判断，但不再作为基础总分权重。

如果某条机会已被用户主动核验，排序会额外考虑：

- `verified` 且 `credibility_score` 高：小幅加权。
- `uncertain`：不加或轻微降权。
- `suspicious` / `high risk`：明显降权。
- 未核验：不惩罚，只在前端标记“未核验”。

前端推荐排序页只展示排序后的机会顺序，不展示 `total_score`、分项星级、机会类型、风险提示或反推荐理由。推荐卡片保留：

- 当前状态
- 截止时间、紧急程度
- `内容概括`：由 DeepSeek/OpenAI 在推荐生成阶段生成，要求简洁、一眼可懂；没有 Key 或调用失败时使用本地规则 fallback
- `相关性`：由 DeepSeek/OpenAI 分析文章内容与用户目标、技能、兴趣、地点/时间偏好的关系；没有 Key 或调用失败时使用本地规则 fallback
- 行动建议和工具/反馈按钮

推荐生成阶段的模型输出会保存到 `content_overview` 和 `relevance_explanation`，同时可返回 `relevance_score`、`benefit_score` 作为排序校正信号。排序公式仍由 `matching_service` 控制，避免完全依赖模型不可解释判断。

## 按需工具调用

默认流水线只做文章导入、机会抽取、初步推荐和排序。核验、补全、日历、邮件、待办只在用户点击某条机会时触发。

支持 action：

- `verify`：核验真实性，更新 `verification_status`、`credibility_score`、`risk_level`、`risk_flags` 等字段。
- `enrich`：补全官方链接、报名链接、deadline、地点、要求等字段，无法确认则返回 `unknown`。
- `calendar`：创建 mock 日历提醒。
- `email`：生成咨询/申请邮件草稿，不发送。
- `todo`：生成行动待办清单。

完整调用示例：

1. 用户在推荐卡片看到“AI 实验室春季本科生科研助理招募”。
2. 点击“核验真实性”。
3. 前端调用 `POST /opportunities/1/actions`，请求体为 `{"user_id": 1, "action": "verify", "extra_params": {}}`。
4. `OpportunityAgent` 读取 opportunity 和 user_profile，选择 `VerifyOpportunityTool`。
5. `VerifyOpportunityTool` 通过 `MockSearchProvider` 生成局部证据，并输出可信度、风险等级和摘要。
6. 系统写入 `tool_call_logs`，并更新 opportunity 的核验字段。
7. 推荐卡片展示“已核验：可信度 86 / 风险 low”。

## Growth Memory

本项目只允许两类轻量记忆：

- Reflection Memory：用户行为 -> 行为日志 -> 定期总结 -> Reflection -> 推荐时使用 Reflection。
- Retrieval-Gated Memory：推荐时只根据当前 opportunity 类型取回相关 reflection，不读取全部事件日志。

不做：

- 不保存完整历史对话。
- 不把全部用户行为塞进 Prompt。
- 不引入向量数据库。
- 不构建聊天机器人式 Memory。

推荐流程从：

```text
profile + opportunity -> recommendation
```

升级为：

```text
profile + relevant_memory + opportunity -> recommendation
```

其中 `relevant_memory` 来自 `memory_reflections`，不是 `user_events` 原始日志。

## API

- `POST /profiles`
- `GET /profiles`
- `GET /profiles/{user_id}`
- `PUT /profiles/{user_id}`
- `DELETE /profiles/{user_id}`
- `POST /articles/import`
- `GET /articles`
- `POST /pipeline/process`
- `GET /opportunities`
- `POST /recommendations/generate/{user_id}`
- `GET /recommendations/{user_id}`
- `POST /summaries/generate/{user_id}`
- `GET /summaries/{user_id}`
- `POST /tools/{tool_name}`
- `POST /opportunities/{opportunity_id}/actions`
- `POST /opportunities/{opportunity_id}/actions/verify`
- `POST /opportunities/{opportunity_id}/actions/enrich`
- `POST /opportunities/{opportunity_id}/actions/calendar`
- `POST /opportunities/{opportunity_id}/actions/email`
- `POST /opportunities/{opportunity_id}/actions/todo`
- `POST /memory/events`
- `GET /memory/events/{user_id}`
- `POST /memory/reflections/generate`
- `GET /memory/reflections/{user_id}`
