from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.models.entities import Article, Opportunity, Recommendation, Summary, UserProfile
from app.schemas.dto import (
    ArticleOut,
    ImportRequest,
    ImportResult,
    OpportunityOut,
    ProcessResult,
    RecommendationOut,
    SummaryOut,
    ToolCallOut,
    ToolCallRequest,
    UserProfileCreate,
    UserProfileOut,
)
from app.services.extraction_service import ExtractionService
from app.services.import_service import ImportService
from app.services.json_utils import dumps
from app.services.matching_service import MatchingService
from app.services.summary_service import SummaryService
from app.services.tool_service import ToolService

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/profiles", response_model=UserProfileOut)
def create_profile(payload: UserProfileCreate, db: Session = Depends(get_db)) -> UserProfile:
    profile = UserProfile(
        name=payload.name,
        major_direction=payload.major_direction,
        grade_identity=payload.grade_identity,
        current_goals=dumps(payload.current_goals),
        skills=dumps(payload.skills),
        interested_fields=dumps(payload.interested_fields),
        disliked_contents=dumps(payload.disliked_contents),
        time_preference=payload.time_preference,
        location_preference=payload.location_preference,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/profiles", response_model=list[UserProfileOut])
def list_profiles(db: Session = Depends(get_db)) -> list[UserProfile]:
    return db.query(UserProfile).order_by(UserProfile.id.desc()).all()


@router.post("/articles/import", response_model=ImportResult)
def import_articles(payload: ImportRequest, db: Session = Depends(get_db)) -> ImportResult:
    try:
        path = str(Path(payload.path))
        articles = ImportService().import_file(db, path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ImportResult(imported=len(articles), article_ids=[article.id for article in articles])


@router.get("/articles", response_model=list[ArticleOut])
def list_articles(db: Session = Depends(get_db)) -> list[Article]:
    return db.query(Article).order_by(Article.imported_at.desc()).all()


@router.post("/pipeline/process", response_model=ProcessResult)
def process_pipeline(db: Session = Depends(get_db)) -> ProcessResult:
    processed, created = ExtractionService().process_unprocessed_articles(db)
    return ProcessResult(processed_articles=processed, created_opportunities=created)


@router.get("/opportunities", response_model=list[OpportunityOut])
def list_opportunities(category: str | None = None, db: Session = Depends(get_db)) -> list[Opportunity]:
    query = db.query(Opportunity).order_by(Opportunity.created_at.desc())
    if category:
        query = query.filter(Opportunity.category == category)
    return query.all()


@router.post("/recommendations/generate/{user_id}", response_model=list[RecommendationOut])
def generate_recommendations(user_id: int, db: Session = Depends(get_db)) -> list[Recommendation]:
    try:
        return MatchingService().generate_for_user(db, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/recommendations/{user_id}", response_model=list[RecommendationOut])
def list_recommendations(user_id: int, db: Session = Depends(get_db)) -> list[Recommendation]:
    return (
        db.query(Recommendation)
        .options(joinedload(Recommendation.opportunity))
        .filter(Recommendation.user_id == user_id)
        .order_by(Recommendation.total_score.desc())
        .all()
    )


@router.post("/summaries/generate/{user_id}", response_model=SummaryOut)
def generate_summary(user_id: int, period: str = "daily", db: Session = Depends(get_db)) -> Summary:
    try:
        return SummaryService().generate(db, user_id, period)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/summaries/{user_id}", response_model=list[SummaryOut])
def list_summaries(user_id: int, db: Session = Depends(get_db)) -> list[Summary]:
    return db.query(Summary).filter(Summary.user_id == user_id).order_by(Summary.created_at.desc()).all()


@router.post("/tools/{tool_name}", response_model=ToolCallOut)
def call_tool(tool_name: str, payload: ToolCallRequest, db: Session = Depends(get_db)):
    try:
        return ToolService().call(db, tool_name, payload.user_id, payload.opportunity_id, payload.payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
