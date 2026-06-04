from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
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
    time_preference: Mapped[str] = mapped_column(String(255), default="")
    location_preference: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    recommendations: Mapped[list["Recommendation"]] = relationship(back_populates="user")
    summaries: Mapped[list["Summary"]] = relationship(back_populates="user")


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
    action_suggestion: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[UserProfile] = relationship(back_populates="recommendations")
    opportunity: Mapped[Opportunity] = relationship(back_populates="recommendations")


class Summary(Base):
    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user_profiles.id"), index=True)
    period: Mapped[str] = mapped_column(String(40), default="daily")
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped[UserProfile] = relationship(back_populates="summaries")


class ToolCall(Base):
    __tablename__ = "tool_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user_profiles.id"), nullable=True)
    opportunity_id: Mapped[int | None] = mapped_column(ForeignKey("opportunities.id"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(40), default="mocked")
    result: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
