from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class UserProfileBase(BaseModel):
    name: str = "默认用户"
    major_direction: str = ""
    grade_identity: str = ""
    current_goals: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    interested_fields: list[str] = Field(default_factory=list)
    disliked_contents: list[str] = Field(default_factory=list)
    detailed_needs: str = ""
    time_preference: str = ""
    location_preference: str = ""


class UserProfileCreate(UserProfileBase):
    pass


class UserProfileUpdate(UserProfileBase):
    pass


class UserProfileOut(UserProfileBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @model_validator(mode="before")
    @classmethod
    def unpack_json_strings(cls, value):
        if not hasattr(value, "__dict__"):
            return value
        import json

        data = {
            "id": value.id,
            "name": value.name,
            "major_direction": value.major_direction,
            "grade_identity": value.grade_identity,
            "detailed_needs": getattr(value, "detailed_needs", ""),
            "time_preference": value.time_preference,
            "location_preference": value.location_preference,
            "created_at": value.created_at,
            "updated_at": value.updated_at,
        }
        for field in ["current_goals", "skills", "interested_fields", "disliked_contents"]:
            try:
                parsed = json.loads(getattr(value, field) or "[]")
            except json.JSONDecodeError:
                parsed = []
            data[field] = parsed if isinstance(parsed, list) else []
        return data


class ArticleCreate(BaseModel):
    title: str
    source: str = "本地导入"
    author: str = ""
    published_at: str = ""
    content: str
    url: str = ""


class ArticleOut(ArticleCreate):
    id: int
    imported_at: datetime
    processed: bool

    model_config = {"from_attributes": True}


class ImportRequest(BaseModel):
    path: str


class ImportResult(BaseModel):
    imported: int
    article_ids: list[int]


class CleanArticleIngestRequest(BaseModel):
    title: str = ""
    source: str = "公众号自动采集"
    author: str = ""
    published_at: str = ""
    content: str
    url: str = ""
    content_hash: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CleanArticleIngestResult(BaseModel):
    created: bool
    duplicate: bool
    article_id: int | None = None
    content_hash: str
    message: str


class ArticleDeleteResult(BaseModel):
    deleted: bool
    article_id: int
    deleted_opportunities: int = 0
    deleted_recommendations: int = 0
    message: str


class SourceArticleOut(BaseModel):
    opportunity_id: int
    opportunity_name: str
    article_id: int | None = None
    article_title: str = ""
    source: str = ""
    published_at: str = ""
    original_url: str = ""
    content: str = ""
    fallback_used: bool = False


class OpportunityOut(BaseModel):
    id: int
    article_id: int
    name: str
    category: str
    organizer: str
    event_time: str
    deadline: str
    location: str
    target_audience: str
    link: str
    requirements: str
    summary: str
    benefit: float
    cost: float
    credibility: float
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    official_url: str = ""
    registration_url: str = ""
    verification_status: str = ""
    credibility_score: float | None = None
    risk_level: str = ""
    risk_flags: list[str] = Field(default_factory=list)
    verification_summary: str = ""
    evidence_sources: list[dict[str, Any]] = Field(default_factory=list)
    enriched_at: datetime | None = None
    verified_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("raw_payload", mode="before")
    @classmethod
    def parse_raw_payload(cls, value):
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @field_validator("risk_flags", "evidence_sources", mode="before")
    @classmethod
    def parse_json_list(cls, value):
        if isinstance(value, list):
            return value
        if not value:
            return []
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []


class RecommendationOut(BaseModel):
    id: int
    user_id: int
    opportunity_id: int
    opportunity: OpportunityOut
    relevance_score: float
    urgency_score: float
    benefit_score: float
    cost_score: float
    credibility_score: float
    total_score: float
    reason: str
    content_overview: str = ""
    relevance_explanation: str = ""
    risk_notes: list[str] = Field(default_factory=list)
    anti_recommendation_reason: str = ""
    action_suggestion: str
    opportunity_state: str = "recommended"
    deadline: str = ""
    deadline_urgency: str = "unknown"
    deadline_note: str = ""
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("risk_notes", mode="before")
    @classmethod
    def parse_risk_notes(cls, value):
        if isinstance(value, list):
            return value
        if not value:
            return []
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []


class OpportunityStateRequest(BaseModel):
    user_id: int
    state: str


class OpportunityStatePatchRequest(BaseModel):
    state: str


class OpportunityStateOut(BaseModel):
    user_id: int
    opportunity_id: int
    state: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProcessResult(BaseModel):
    processed_articles: int
    created_opportunities: int


class ToolCallRequest(BaseModel):
    user_id: int | None = None
    opportunity_id: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolCallOut(BaseModel):
    id: int
    tool_name: str
    status: str
    result: dict[str, Any]
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("result", mode="before")
    @classmethod
    def parse_result(cls, value):
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}


class ToolErrorOut(BaseModel):
    status: str
    error_message: str


class EnrichmentResultOut(BaseModel):
    id: int
    user_id: int
    opportunity_id: int
    query: str
    summary: str
    key_findings: list[Any] = Field(default_factory=list)
    official_links: list[Any] = Field(default_factory=list)
    registration_links: list[Any] = Field(default_factory=list)
    deadline_notes: str = ""
    eligibility_notes: str = ""
    risk_flags: list[Any] = Field(default_factory=list)
    credibility_score: float | None = None
    action_suggestion: str = ""
    sources: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("key_findings", "official_links", "registration_links", "risk_flags", "sources", mode="before")
    @classmethod
    def parse_json_list_fields(cls, value):
        if isinstance(value, list):
            return value
        if not value:
            return []
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []


class SimilarOpportunityResultOut(BaseModel):
    id: int
    user_id: int
    opportunity_id: int
    query: str
    results: list[dict[str, Any]] = Field(default_factory=list)
    comparison_summary: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("results", mode="before")
    @classmethod
    def parse_results(cls, value):
        if isinstance(value, list):
            return value
        if not value:
            return []
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []


class OpportunityAssistantChatRequest(BaseModel):
    user_id: int
    opportunity_id: int
    message: str
    use_web_search: bool = False
    include_memory: bool = True


class OpportunityAssistantChatResponse(BaseModel):
    answer: str
    used_search: bool = False
    search_status: str = "none"
    sources: list[dict[str, Any]] = Field(default_factory=list)
    memory_updated: bool = False


class TodoOut(BaseModel):
    id: int
    user_id: int
    opportunity_id: int
    title: str
    description: str
    deadline: str | None = None
    deadline_note: str = ""
    status: str
    priority: str
    created_at: datetime
    updated_at: datetime
    already_exists: bool = False
    short_title: str = ""
    date_uncertain_reason: str = ""

    model_config = {"from_attributes": True}


class CalendarEventOut(BaseModel):
    id: int
    user_id: int
    opportunity_id: int
    title: str
    start_time: datetime | None = None
    end_time: datetime | None = None
    status: str
    source: str
    opportunity_name: str = ""
    short_title: str = ""
    reminder_type: str = ""
    display_date_text: str = ""
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class CalendarDashboardOut(BaseModel):
    upcoming_7_days: list[CalendarEventOut] = Field(default_factory=list)
    later_events: list[CalendarEventOut] = Field(default_factory=list)
    expired_events: list[CalendarEventOut] = Field(default_factory=list)
    uncertain_deadline_todos: list[TodoOut] = Field(default_factory=list)


class RecommendationFeedbackRequest(BaseModel):
    event_type: str
    opportunity_id: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpportunityActionRequest(BaseModel):
    user_id: int
    action: str = ""
    extra_params: dict[str, Any] = Field(default_factory=dict)


class OpportunityActionResponse(BaseModel):
    action: str
    status: str
    tool_name: str
    requires_confirmation: bool = False
    result: dict[str, Any]
    log_id: int
    opportunity_state: str = ""


class UserEventCreate(BaseModel):
    user_id: int
    event_type: str
    event_target: str = ""
    event_metadata: dict[str, Any] = Field(default_factory=dict)


class UserEventOut(BaseModel):
    id: int
    user_id: int
    event_type: str
    event_target: str
    event_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("event_metadata", mode="before")
    @classmethod
    def parse_event_metadata(cls, value):
        if isinstance(value, dict):
            return value
        if not value:
            return {}
        import json

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}


class GrowthMemorySnapshotOut(BaseModel):
    id: int
    user_id: int
    current_stage: str
    recent_focus: str
    preference_changes: str
    overall_observation: str
    next_stage_advice: str
    behavior_count: int = 0
    behavior_start_time: datetime | None = None
    behavior_end_time: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class GrowthMemoryResponse(BaseModel):
    success: bool = True
    updated: bool = False
    message: str = ""
    data: GrowthMemorySnapshotOut | None = None


class OpportunityProgressRoundOut(BaseModel):
    stage: str = ""
    stage_title: str = ""
    round_summary: str = ""
    findings: list[str] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)
    next_focus: str = ""
    should_continue: bool = False
    stop_reason: str | None = None


class OpportunityProgressTodoOut(BaseModel):
    title: str = ""
    description: str = ""


class OpportunityProgressFinalOut(BaseModel):
    overall_judgment: str = ""
    summary: str = ""
    source_assessment: str = ""
    similar_opportunity_comparison: str = ""
    user_fit: str = ""
    advantages: list[str] = Field(default_factory=list)
    risks_and_uncertainties: list[str] = Field(default_factory=list)
    recommended_strategy: str = ""
    suggested_todos: list[OpportunityProgressTodoOut] = Field(default_factory=list)
    should_create_todos: bool = False
    stop_reason: str | None = None


class OpportunityProgressTaskOut(BaseModel):
    id: int
    user_id: int
    opportunity_id: int
    current_stage: str = ""
    rounds: list[OpportunityProgressRoundOut] = Field(default_factory=list)
    final_result: OpportunityProgressFinalOut | None = None
    suggested_todos: list[OpportunityProgressTodoOut] = Field(default_factory=list)
    confirmed: bool = False
    created_todo_ids: list[int] = Field(default_factory=list)
    status: str = ""
    error_message: str = ""
    created_at: datetime
    updated_at: datetime


class OpportunityProgressAnalyzeResponse(BaseModel):
    success: bool = True
    message: str = ""
    task: OpportunityProgressTaskOut


class OpportunityProgressConfirmResponse(BaseModel):
    success: bool = True
    message: str = ""
    task: OpportunityProgressTaskOut
    todo: TodoOut | None = None
