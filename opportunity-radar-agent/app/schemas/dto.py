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
    time_preference: str = ""
    location_preference: str = ""


class UserProfileCreate(UserProfileBase):
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
    action_suggestion: str
    created_at: datetime

    model_config = {"from_attributes": True}


class SummaryOut(BaseModel):
    id: int
    user_id: int
    period: str
    content: str
    created_at: datetime

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
