from collections.abc import Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models import entities  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_sqlite_columns()


def _ensure_sqlite_columns() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    if "user_profiles" not in table_names:
        return
    existing = {column["name"] for column in inspector.get_columns("user_profiles")}
    with engine.begin() as connection:
        if "detailed_needs" not in existing:
            connection.execute(text("ALTER TABLE user_profiles ADD COLUMN detailed_needs TEXT DEFAULT ''"))
    if "opportunities" not in table_names:
        return
    existing_opp = {column["name"] for column in inspector.get_columns("opportunities")}
    opportunity_columns = {
        "official_url": "TEXT DEFAULT ''",
        "registration_url": "TEXT DEFAULT ''",
        "verification_status": "VARCHAR(40) DEFAULT ''",
        "credibility_score": "FLOAT",
        "risk_level": "VARCHAR(40) DEFAULT ''",
        "risk_flags": "TEXT DEFAULT '[]'",
        "verification_summary": "TEXT DEFAULT ''",
        "evidence_sources": "TEXT DEFAULT '[]'",
        "enriched_at": "DATETIME",
        "verified_at": "DATETIME",
    }
    with engine.begin() as connection:
        for column_name, column_type in opportunity_columns.items():
            if column_name not in existing_opp:
                connection.execute(text(f"ALTER TABLE opportunities ADD COLUMN {column_name} {column_type}"))
    if "recommendations" not in table_names:
        return
    existing_rec = {column["name"] for column in inspector.get_columns("recommendations")}
    recommendation_columns = {
        "content_overview": "TEXT DEFAULT ''",
        "relevance_explanation": "TEXT DEFAULT ''",
        "risk_notes": "TEXT DEFAULT '[]'",
        "anti_recommendation_reason": "TEXT DEFAULT ''",
        "opportunity_state": "VARCHAR(40) DEFAULT 'recommended'",
        "deadline": "VARCHAR(120) DEFAULT ''",
        "deadline_urgency": "VARCHAR(40) DEFAULT 'unknown'",
        "deadline_note": "TEXT DEFAULT ''",
    }
    with engine.begin() as connection:
        for column_name, column_type in recommendation_columns.items():
            if column_name not in existing_rec:
                connection.execute(text(f"ALTER TABLE recommendations ADD COLUMN {column_name} {column_type}"))
