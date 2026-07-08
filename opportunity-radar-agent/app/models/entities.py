from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), default="默认用户")
    major_direction: Mapped[str] = mapped_column(String(255), default="")
    grade_identity: Mapped[str] = mapped_column(String(120), default="")
    current_goals: Mapped[str] = mapped_column(Text, default="[]")
    skills: Mapped[str] = mapped_column(Text, default="[]")
    interested_fields: Mapped[str] = mapped_column(Text, default="[]")
    disliked_contents: Mapped[str] = mapped_column(Text, default="[]")
    detailed_needs: Mapped[str] = mapped_column(Text, default="")
    time_preference: Mapped[str] = mapped_column(String(255), default="")
    location_preference: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="user")

class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    source: Mapped[str] = mapped_column(String(120), default="本地导入")
    author: Mapped[str] = mapped_column(String(120), default="")
    published_at: Mapped[str] = mapped_column(String(64), default="")
    content: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(String(500), default="")
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)

    opportunities: Mapped[list["Opportunity"]] = relationship(back_populates="article")


class Opportunity(Base):
    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(80), index=True)
    organizer: Mapped[str] = mapped_column(String(255), default="")
    event_time: Mapped[str] = mapped_column(String(120), default="")
    deadline: Mapped[str] = mapped_column(String(120), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    target_audience: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(500), default="")
    requirements: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    benefit: Mapped[float] = mapped_column(Float, default=0.6)
    cost: Mapped[float] = mapped_column(Float, default=0.4)
    credibility: Mapped[float] = mapped_column(Float, default=0.7)
    raw_payload: Mapped[str] = mapped_column(Text, default="{}")
    official_url: Mapped[str] = mapped_column(String(500), default="")
    registration_url: Mapped[str] = mapped_column(String(500), default="")
    verification_status: Mapped[str] = mapped_column(String(40), default="")
    credibility_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(40), default="")
    risk_flags: Mapped[str] = mapped_column(Text, default="[]")
    verification_summary: Mapped[str] = mapped_column(Text, default="")
    evidence_sources: Mapped[str] = mapped_column(Text, default="[]")
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    article: Mapped[Article] = relationship(back_populates="opportunities")
    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="opportunity")


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0)
    urgency_score: Mapped[float] = mapped_column(Float, default=0)
    benefit_score: Mapped[float] = mapped_column(Float, default=0)
    cost_score: Mapped[float] = mapped_column(Float, default=0)
    credibility_score: Mapped[float] = mapped_column(Float, default=0)
    total_score: Mapped[float] = mapped_column(Float, default=0)
    reason: Mapped[str] = mapped_column(Text, default="")
    content_overview: Mapped[str] = mapped_column(Text, default="")
    relevance_explanation: Mapped[str] = mapped_column(Text, default="")
    risk_notes: Mapped[str] = mapped_column(Text, default="[]")
    anti_recommendation_reason: Mapped[str] = mapped_column(Text, default="")
    action_suggestion: Mapped[str] = mapped_column(Text, default="")
    opportunity_state: Mapped[str] = mapped_column(String(40), default="recommended")
    deadline: Mapped[str] = mapped_column(String(120), default="")
    deadline_urgency: Mapped[str] = mapped_column(String(40), default="unknown")
    deadline_note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[UserProfile] = relationship(back_populates="recommendations")
    opportunity: Mapped[Opportunity] = relationship(back_populates="recommendations")


class OpportunityState(Base):
    __tablename__ = "opportunity_states"
    __table_args__ = (UniqueConstraint("user_id", "opportunity_id", name="uq_opportunity_state_user_opp"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    state: Mapped[str] = mapped_column(String(40), default="new", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Todo(Base):
    __tablename__ = "todos"
    __table_args__ = (UniqueConstraint("user_id", "opportunity_id", name="uq_todo_user_opportunity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    deadline: Mapped[str | None] = mapped_column(String(120), nullable=True)
    deadline_note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    priority: Mapped[str] = mapped_column(String(40), default="medium", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class CalendarEvent(Base):
    __tablename__ = "calendar_events"
    __table_args__ = (UniqueConstraint("user_id", "opportunity_id", name="uq_calendar_event_user_opportunity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="scheduled", index=True)
    source: Mapped[str] = mapped_column(String(120), default="recommendation_card")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user_profiles.id"), nullable=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    query: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(40), default="success")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ToolCallLog(Base):
    __tablename__ = "tool_call_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    action: Mapped[str] = mapped_column(String(80), index=True)
    tool_name: Mapped[str] = mapped_column(String(120), index=True)
    query: Mapped[str] = mapped_column(Text, default="")
    provider: Mapped[str] = mapped_column(String(120), default="")
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    output_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(40), default="success")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class EnrichmentResult(Base):
    __tablename__ = "enrichment_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    query: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    key_findings: Mapped[str] = mapped_column(Text, default="[]")
    official_links: Mapped[str] = mapped_column(Text, default="[]")
    registration_links: Mapped[str] = mapped_column(Text, default="[]")
    deadline_notes: Mapped[str] = mapped_column(Text, default="")
    eligibility_notes: Mapped[str] = mapped_column(Text, default="")
    risk_flags: Mapped[str] = mapped_column(Text, default="[]")
    credibility_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    action_suggestion: Mapped[str] = mapped_column(Text, default="")
    sources: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(40), default="success", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SimilarOpportunityResult(Base):
    __tablename__ = "similar_opportunity_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    query: Mapped[str] = mapped_column(Text, default="")
    results: Mapped[str] = mapped_column(Text, default="[]")
    comparison_summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="success", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SearchCache(Base):
    __tablename__ = "search_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    provider: Mapped[str] = mapped_column(String(120), index=True)
    query: Mapped[str] = mapped_column(Text, default="", index=True)
    results: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(80), default="", index=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class UserEvent(Base):
    __tablename__ = "user_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    event_target: Mapped[str] = mapped_column(String(120), default="")
    event_metadata: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class MemoryReflection(Base):
    __tablename__ = "memory_reflections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    reflection_type: Mapped[str] = mapped_column(String(120), index=True)
    summary: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GrowthMemorySnapshot(Base):
    __tablename__ = "growth_memory_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    current_stage: Mapped[str] = mapped_column(Text, default="")
    recent_focus: Mapped[str] = mapped_column(Text, default="")
    preference_changes: Mapped[str] = mapped_column(Text, default="")
    overall_observation: Mapped[str] = mapped_column(Text, default="")
    next_stage_advice: Mapped[str] = mapped_column(Text, default="")
    behavior_count: Mapped[int] = mapped_column(Integer, default=0)
    behavior_start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    behavior_end_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    analyzed_event_ids: Mapped[str] = mapped_column(Text, default="[]")
    referenced_memory_ids: Mapped[str] = mapped_column(Text, default="[]")
    model_name: Mapped[str] = mapped_column(String(120), default="")
    prompt_version: Mapped[str] = mapped_column(String(80), default="growth_memory_v2")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class OpportunityProgressTask(Base):
    __tablename__ = "opportunity_progress_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    current_stage: Mapped[str] = mapped_column(String(80), default="initial_analysis", index=True)
    rounds: Mapped[str] = mapped_column(Text, default="[]")
    final_result: Mapped[str] = mapped_column(Text, default="{}")
    suggested_todos: Mapped[str] = mapped_column(Text, default="[]")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_todo_ids: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(40), default="analyzing", index=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OpportunityProgressPlan(Base):
    __tablename__ = "opportunity_progress_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    source_task_id: Mapped[int | None] = mapped_column(ForeignKey("opportunity_progress_tasks.id"), nullable=True, index=True)
    goal: Mapped[str] = mapped_column(Text, default="")
    overall_judgment: Mapped[str] = mapped_column(Text, default="")
    current_stage: Mapped[str] = mapped_column(Text, default="")
    plan_summary: Mapped[str] = mapped_column(Text, default="")
    next_focus: Mapped[str] = mapped_column(Text, default="")
    risks_json: Mapped[str] = mapped_column(Text, default="[]")
    system_status: Mapped[str] = mapped_column(String(40), default="active", index=True)
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OpportunityProgressItem(Base):
    __tablename__ = "opportunity_progress_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("opportunity_progress_plans.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[str] = mapped_column(String(40), default="medium", index=True)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    source: Mapped[str] = mapped_column(String(80), default="ai_plan")
    synced_to_todo: Mapped[bool] = mapped_column(Boolean, default=False)
    todo_id: Mapped[int | None] = mapped_column(ForeignKey("todos.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OpportunityProgressUpdate(Base):
    __tablename__ = "opportunity_progress_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("opportunity_progress_plans.id"), index=True)
    user_progress_text: Mapped[str] = mapped_column(Text, default="")
    ai_assessment_json: Mapped[str] = mapped_column(Text, default="{}")
    proposed_changes_json: Mapped[str] = mapped_column(Text, default="{}")
    confirmation_status: Mapped[str] = mapped_column(String(40), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class OpportunityProgressVersion(Base):
    __tablename__ = "opportunity_progress_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("opportunity_progress_plans.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer, default=1, index=True)
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    update_id: Mapped[int | None] = mapped_column(ForeignKey("opportunity_progress_updates.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class OpportunityProgressWebCheck(Base):
    __tablename__ = "opportunity_progress_web_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    opportunity_id: Mapped[int] = mapped_column(ForeignKey("opportunities.id"), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("opportunity_progress_tasks.id"), nullable=True, index=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey("opportunity_progress_plans.id"), nullable=True, index=True)
    query: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="searching", index=True)
    sources_json: Mapped[str] = mapped_column(Text, default="[]")
    analysis_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str] = mapped_column(Text, default="")
    request_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
