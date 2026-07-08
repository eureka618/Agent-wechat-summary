from datetime import datetime, timedelta
import logging
from pathlib import Path
import threading
import time
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.core.database import SessionLocal, get_db

from app.models.entities import (
    Article,
    CalendarEvent,
    EnrichmentResult,
    GrowthMemorySnapshot,
    MemoryReflection,
    Opportunity,
    OpportunityProgressTask,
    OpportunityState,
    Recommendation,
    SimilarOpportunityResult,
    Todo,
    ToolCall,
    ToolCallLog,
    UserEvent,
    UserProfile,
)
from app.schemas.dto import (
    ArticleDeleteResult,
    ArticleOut,
    CalendarDashboardOut,
    CalendarEventOut,
    CleanArticleIngestRequest,
    CleanArticleIngestResult,
    EnrichmentResultOut,
    RecommendationFeedbackRequest,
    ImportRequest,
    ImportResult,
    OpportunityAssistantChatRequest,
    OpportunityAssistantChatResponse,
    OpportunityProgressAnalyzeResponse,
    OpportunityProgressConfirmResponse,
    OpportunityOut,
    OpportunityStateOut,
    OpportunityStatePatchRequest,
    ProcessResult,
    RecommendationOut,
    SourceArticleOut,
    SimilarOpportunityResultOut,
    TodoOut,
    ToolCallOut,
    ToolCallRequest,
    UserEventOut,
    UserProfileCreate,
    UserProfileOut,
    UserProfileUpdate,
)
from app.services.article_ingest_service import ArticleIngestService
from app.services.article_service import ArticleService
from app.services.calendar_event_service import CalendarEventService
from app.services.extraction_service import ExtractionService
from app.services.feedback_service import FeedbackService
from app.services.import_service import ImportService
from app.services.json_utils import dumps
from app.services.llm_gateway import LLMGateway
from app.services.matching_service import MatchingService
from app.services.opportunity_assistant_service import OpportunityAssistantService
from app.services.opportunity_progress_service import OpportunityProgressService
from app.services.opportunity_state_service import OpportunityStateService
from app.services.search_provider import WeixinSearchProvider
from app.services.search_enrichment_service import SearchEnrichmentService
from app.services.similar_opportunity_search_service import SimilarOpportunitySearchService
from app.services.todo_service import TodoService
from app.services.tool_service import ToolService

router = APIRouter()
logger = logging.getLogger(__name__)
_recommendation_refresh_locks: set[int] = set()
_recommendation_refresh_guard = threading.Lock()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/llm/config")
def llm_config() -> dict:
    return LLMGateway().debug_config()


@router.post("/llm/test")
def test_llm() -> dict:
    try:
        content = LLMGateway().chat(
            messages=[
                {"role": "system", "content": "你是一个简洁的连通性测试助手。"},
                {"role": "user", "content": "你好"},
            ],
            temperature=0.1,
            max_retries=0,
        )
        return {"ok": True, "content": content}
    except RuntimeError as exc:
        logger.exception("llm test failed")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("llm test unexpected failure")
        raise HTTPException(status_code=500, detail="LLM test failed unexpectedly: {}".format(exc)) from exc


@router.post("/profiles", response_model=UserProfileOut)
def create_profile(payload: UserProfileCreate, db: Session = Depends(get_db)) -> UserProfile:
    try:
        profile = UserProfile(
            name=payload.name,
            major_direction=payload.major_direction,
            grade_identity=payload.grade_identity,
            current_goals=dumps(payload.current_goals),
            skills=dumps(payload.skills),
            interested_fields=dumps(payload.interested_fields),
            disliked_contents=dumps(payload.disliked_contents),
            detailed_needs=payload.detailed_needs,
            time_preference=payload.time_preference,
            location_preference=payload.location_preference,
        )
        db.add(profile)
        db.commit()
        db.refresh(profile)
        return profile
    except Exception as exc:
        logger.exception("create profile failed")
        raise HTTPException(status_code=500, detail="用户画像创建失败") from exc


@router.get("/profiles", response_model=list[UserProfileOut])
def list_profiles(db: Session = Depends(get_db)) -> list[UserProfile]:
    try:
        return db.query(UserProfile).order_by(UserProfile.id.desc()).all()
    except Exception as exc:
        logger.exception("list profiles failed")
        raise HTTPException(status_code=500, detail="用户画像列表读取失败") from exc


@router.get("/profiles/{user_id}", response_model=UserProfileOut)
def get_profile(user_id: int, db: Session = Depends(get_db)) -> UserProfile:
    try:
        profile = db.get(UserProfile, user_id)
        if not profile:
            raise HTTPException(status_code=404, detail="用户画像不存在")
        return profile
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("get profile failed user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="用户画像读取失败") from exc


@router.put("/profiles/{user_id}", response_model=UserProfileOut)
def update_profile(user_id: int, payload: UserProfileUpdate, db: Session = Depends(get_db)) -> UserProfile:
    try:
        profile = db.get(UserProfile, user_id)
        if not profile:
            raise HTTPException(status_code=404, detail="用户画像不存在")
        profile.name = payload.name
        profile.major_direction = payload.major_direction
        profile.grade_identity = payload.grade_identity
        profile.current_goals = dumps(payload.current_goals)
        profile.skills = dumps(payload.skills)
        profile.interested_fields = dumps(payload.interested_fields)
        profile.disliked_contents = dumps(payload.disliked_contents)
        profile.detailed_needs = payload.detailed_needs
        profile.time_preference = payload.time_preference
        profile.location_preference = payload.location_preference
        profile.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(profile)
        return profile
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("update profile failed user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="用户画像更新失败") from exc


@router.delete("/profiles/{user_id}")
def delete_profile(user_id: int, db: Session = Depends(get_db)) -> dict[str, int | str]:
    try:
        profile = db.get(UserProfile, user_id)
        if not profile:
            raise HTTPException(status_code=404, detail="用户画像不存在")
        for model in [
            Recommendation,
            OpportunityState,
            ToolCallLog,
            ToolCall,
            Todo,
            CalendarEvent,
            EnrichmentResult,
            SimilarOpportunityResult,
            UserEvent,
            MemoryReflection,
            GrowthMemorySnapshot,
        ]:
            db.query(model).filter(model.user_id == user_id).delete(synchronize_session=False)
        db.delete(profile)
        db.commit()
        return {"status": "deleted", "user_id": user_id}
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("delete profile failed user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="用户画像删除失败") from exc


@router.post("/articles/import", response_model=ImportResult)
def import_articles(payload: ImportRequest, db: Session = Depends(get_db)) -> ImportResult:
    try:
        path = str(Path(payload.path))
        articles = ImportService().import_file(db, path)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("article import failed path=%s", payload.path)
        raise HTTPException(status_code=500, detail="文章导入失败") from exc
    logger.info("articles imported count=%s path=%s", len(articles), payload.path)
    return ImportResult(imported=len(articles), article_ids=[article.id for article in articles])


@router.post("/articles/ingest-clean", response_model=CleanArticleIngestResult)
def ingest_clean_article(payload: CleanArticleIngestRequest, db: Session = Depends(get_db)) -> CleanArticleIngestResult:
    try:
        result = ArticleIngestService().ingest_clean_article(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("clean article ingest failed title=%s", payload.title)
        raise HTTPException(status_code=500, detail="文章入库失败") from exc

    logger.info(
        "clean article ingest result created=%s duplicate=%s article_id=%s",
        result.get("created"),
        result.get("duplicate"),
        result.get("article_id"),
    )
    return CleanArticleIngestResult(**result)


@router.get("/articles", response_model=list[ArticleOut])
def list_articles(db: Session = Depends(get_db)) -> list[Article]:
    try:
        return ArticleService().list_articles(db)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("list articles failed")
        raise HTTPException(status_code=500, detail="文章列表读取失败") from exc


@router.delete("/articles/{article_id}", response_model=ArticleDeleteResult)
def delete_article(article_id: int, db: Session = Depends(get_db)) -> ArticleDeleteResult:
    try:
        result = ArticleService().delete_article(db, article_id)
    except ValueError as exc:
        if str(exc).startswith("Unsupported"):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("delete article failed article_id=%s", article_id)
        raise HTTPException(status_code=500, detail="文章删除失败") from exc
    return ArticleDeleteResult(**result)


@router.post("/pipeline/process", response_model=ProcessResult)
def process_pipeline(db: Session = Depends(get_db)) -> ProcessResult:
    try:
        processed, created = ExtractionService().process_unprocessed_articles(db)
    except Exception as exc:
        logger.exception("pipeline process failed")
        raise HTTPException(status_code=500, detail="机会抽取处理失败") from exc
    logger.info("pipeline processed_articles=%s created_opportunities=%s", processed, created)
    return ProcessResult(processed_articles=processed, created_opportunities=created)


@router.get("/opportunities", response_model=list[OpportunityOut])
def list_opportunities(category: str | None = None, db: Session = Depends(get_db)) -> list[Opportunity]:
    try:
        query = db.query(Opportunity).order_by(Opportunity.created_at.desc())
        if category:
            query = query.filter(Opportunity.category == category)
        return query.all()
    except Exception as exc:
        logger.exception("list opportunities failed")
        raise HTTPException(status_code=500, detail="机会列表读取失败") from exc


@router.get("/opportunities/{opportunity_id}/source-article", response_model=SourceArticleOut)
def get_opportunity_source_article(opportunity_id: int, db: Session = Depends(get_db)) -> SourceArticleOut:
    try:
        opportunity = db.get(Opportunity, opportunity_id)
        if not opportunity:
            raise HTTPException(status_code=404, detail="机会不存在")
        article = db.get(Article, opportunity.article_id) if opportunity.article_id else None
        if article:
            content = article.content or opportunity.summary or ""
            return SourceArticleOut(
                opportunity_id=opportunity.id,
                opportunity_name=opportunity.name,
                article_id=article.id,
                article_title=article.title or opportunity.name,
                source=article.source or "",
                published_at=article.published_at or "",
                original_url=article.url or opportunity.link or opportunity.official_url or "",
                content=content,
                fallback_used=not bool(article.content),
            )
        fallback_parts = [
            opportunity.summary or "",
            opportunity.requirements or "",
            opportunity.target_audience or "",
            opportunity.deadline or "",
            opportunity.link or opportunity.official_url or "",
        ]
        fallback_content = "\n\n".join(item for item in fallback_parts if item) or "暂未找到完整原文，可先查看机会摘要。"
        return SourceArticleOut(
            opportunity_id=opportunity.id,
            opportunity_name=opportunity.name,
            article_id=opportunity.article_id or None,
            article_title=opportunity.name,
            source="机会摘要",
            published_at="",
            original_url=opportunity.link or opportunity.official_url or "",
            content=fallback_content,
            fallback_used=True,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("source article fallback failed opportunity_id=%s", opportunity_id)
        opportunity = db.get(Opportunity, opportunity_id)
        if not opportunity:
            raise HTTPException(status_code=404, detail="机会不存在") from exc
        return SourceArticleOut(
            opportunity_id=opportunity.id,
            opportunity_name=opportunity.name,
            article_id=opportunity.article_id or None,
            article_title=opportunity.name,
            source="机会摘要",
            published_at="",
            original_url=opportunity.link or opportunity.official_url or "",
            content=opportunity.summary or "暂未找到完整原文，可先查看机会摘要。",
            fallback_used=True,
        )


@router.patch("/opportunity-states/{user_id}/{opportunity_id}", response_model=OpportunityStateOut)
def patch_opportunity_state(
    user_id: int,
    opportunity_id: int,
    payload: OpportunityStatePatchRequest,
    db: Session = Depends(get_db),
):
    try:
        if not db.get(UserProfile, user_id):
            raise ValueError("用户画像不存在")
        opportunity = db.get(Opportunity, opportunity_id)
        if not opportunity:
            raise ValueError("机会不存在")
        state = OpportunityStateService().advance_state(db, user_id, opportunity_id, payload.state)
        if payload.state == "applied":
            db.add(
                UserEvent(
                    user_id=user_id,
                    event_type="apply_opportunity",
                    event_target=str(opportunity_id),
                    event_metadata=dumps(
                        {
                            "source": "recommendation_card",
                            "opportunity_id": opportunity_id,
                            "opportunity_category": opportunity.category,
                            "category": opportunity.category,
                            "opportunity_name": opportunity.name,
                        }
                    ),
                )
            )
        db.commit()
        db.refresh(state)
        logger.info("opportunity state patched user_id=%s opportunity_id=%s state=%s", user_id, opportunity_id, state.state)
        return state
    except ValueError as exc:
        db.rollback()
        if str(exc).startswith("Unsupported"):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("patch opportunity state failed user_id=%s opportunity_id=%s", user_id, opportunity_id)
        raise HTTPException(status_code=500, detail="机会状态更新失败") from exc


@router.post("/todos/from-opportunity/{user_id}/{opportunity_id}", response_model=TodoOut)
def create_todo_from_opportunity(user_id: int, opportunity_id: int, db: Session = Depends(get_db)):
    try:
        return TodoService().create_from_opportunity(db, user_id, opportunity_id)
    except ValueError as exc:
        if str(exc).startswith("Unsupported"):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("create todo from opportunity failed user_id=%s opportunity_id=%s", user_id, opportunity_id)
        raise HTTPException(status_code=500, detail="待办创建失败") from exc


@router.get("/calendar/{user_id}", response_model=CalendarDashboardOut)
def list_calendar_events(user_id: int, db: Session = Depends(get_db)) -> dict:
    try:
        now = datetime.utcnow()
        soon_cutoff = now + timedelta(days=7)
        calendar_service = CalendarEventService()
        events = calendar_service.list_for_user(db, user_id)
        opportunity_ids = {event.opportunity_id for event in events}
        opportunity_map = {
            item.id: item
            for item in db.query(Opportunity).filter(Opportunity.id.in_(opportunity_ids)).all()
        } if opportunity_ids else {}

        upcoming_7_days = []
        later_events = []
        expired_events = []
        for event in events:
            item = _calendar_event_out(event, opportunity_map, calendar_service)
            if event.start_time and event.start_time < now:
                expired_events.append(item)
            elif event.start_time and event.start_time <= soon_cutoff:
                upcoming_7_days.append(item)
            else:
                later_events.append(item)

        return {
            "upcoming_7_days": upcoming_7_days,
            "later_events": later_events,
            "expired_events": expired_events,
            "uncertain_deadline_todos": calendar_service.list_uncertain_deadline_todos(db, user_id),
        }
    except Exception as exc:
        logger.exception("list calendar failed user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="日历提醒读取失败") from exc


def _calendar_event_out(event: CalendarEvent, opportunity_map: dict[int, Opportunity], calendar_service: CalendarEventService) -> dict:
    opportunity = opportunity_map.get(event.opportunity_id)
    extraction = calendar_service.extract_date(opportunity) if opportunity else None
    title_parts = (event.title or "").split("｜", 1)
    reminder_type = title_parts[0] if len(title_parts) == 2 else (calendar_service.reminder_type_label(extraction.date_type) if extraction else "时间提醒")
    short_title = title_parts[1] if len(title_parts) == 2 else calendar_service.short_title(opportunity.name if opportunity else event.title)
    return {
        "id": event.id,
        "user_id": event.user_id,
        "opportunity_id": event.opportunity_id,
        "title": event.title,
        "start_time": event.start_time,
        "end_time": event.end_time,
        "status": event.status,
        "source": event.source,
        "opportunity_name": opportunity.name if opportunity else "",
        "short_title": short_title,
        "reminder_type": reminder_type,
        "display_date_text": extraction.display_date_text if extraction else "",
        "created_at": event.created_at,
        "updated_at": event.updated_at,
    }


@router.post("/feedback/recommendation/{user_id}/{recommendation_id}", response_model=UserEventOut)
def record_recommendation_feedback(
    user_id: int,
    recommendation_id: int,
    payload: RecommendationFeedbackRequest,
    db: Session = Depends(get_db),
):
    try:
        return FeedbackService().record_recommendation_feedback(
            db=db,
            user_id=user_id,
            recommendation_id=recommendation_id,
            event_type=payload.event_type,
            opportunity_id=payload.opportunity_id,
            metadata=payload.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("record recommendation feedback failed user_id=%s recommendation_id=%s", user_id, recommendation_id)
        raise HTTPException(status_code=500, detail="反馈记录失败") from exc


@router.post("/recommendations/generate/{user_id}", response_model=list[RecommendationOut])
def generate_recommendations(
    user_id: int,
    force: bool = True,
    db: Session = Depends(get_db),
) -> list[Recommendation]:
    request_id = str(uuid.uuid4())
    started = time.perf_counter()
    existing = _list_recommendation_rows(db, user_id)
    if force:
        with _recommendation_refresh_guard:
            if user_id in _recommendation_refresh_locks:
                logger.info("recommendation refresh already running request_id=%s user_id=%s", request_id, user_id)
                return existing
            _recommendation_refresh_locks.add(user_id)
        threading.Thread(target=_refresh_recommendations_background, args=(user_id, force, request_id), daemon=True).start()
        logger.info("recommendation refresh scheduled request_id=%s user_id=%s existing_count=%s elapsed_ms=%s", request_id, user_id, len(existing), int((time.perf_counter() - started) * 1000))
        return existing
    try:
        recommendations = MatchingService().generate_for_user(db, user_id, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "not_found", "message": str(exc), "request_id": request_id}) from exc
    except RuntimeError as exc:
        logger.exception("generate recommendations unavailable request_id=%s user_id=%s elapsed_ms=%s", request_id, user_id, int((time.perf_counter() - started) * 1000))
        raise HTTPException(status_code=503, detail={"error_code": "model_unavailable", "message": "AI 服务暂时不可用", "request_id": request_id}) from exc
    except Exception as exc:
        error_code = "database_busy" if "database is locked" in str(exc).lower() else "unknown_error"
        logger.exception("generate recommendations failed request_id=%s user_id=%s elapsed_ms=%s error_type=%s", request_id, user_id, int((time.perf_counter() - started) * 1000), type(exc).__name__)
        raise HTTPException(status_code=503 if error_code == "database_busy" else 500, detail={"error_code": error_code, "message": "系统正在处理其他任务，请稍后重试" if error_code == "database_busy" else "服务处理失败", "request_id": request_id}) from exc
    logger.info("recommendations generated request_id=%s user_id=%s count=%s elapsed_ms=%s", request_id, user_id, len(recommendations), int((time.perf_counter() - started) * 1000))
    return recommendations


@router.post("/recommendations/{user_id}/generate", response_model=list[RecommendationOut])
def generate_recommendations_alias(user_id: int, force: bool = True, db: Session = Depends(get_db)) -> list[Recommendation]:
    return generate_recommendations(user_id=user_id, force=force, db=db)


def _list_recommendation_rows(db: Session, user_id: int) -> list[Recommendation]:
    return (
        db.query(Recommendation)
        .join(Opportunity, Recommendation.opportunity_id == Opportunity.id)
        .options(joinedload(Recommendation.opportunity))
        .filter(Recommendation.user_id == user_id)
        .order_by(Recommendation.total_score.desc())
        .all()
    )


def _refresh_recommendations_background(user_id: int, force: bool, request_id: str) -> None:
    started = time.perf_counter()
    db = SessionLocal()
    try:
        rows = MatchingService().generate_for_user(db, user_id, force=force)
        logger.info("recommendation refresh background completed request_id=%s user_id=%s count=%s elapsed_ms=%s", request_id, user_id, len(rows), int((time.perf_counter() - started) * 1000))
    except Exception as exc:
        db.rollback()
        logger.exception("recommendation refresh background failed request_id=%s user_id=%s elapsed_ms=%s error_type=%s", request_id, user_id, int((time.perf_counter() - started) * 1000), type(exc).__name__)
    finally:
        db.close()
        with _recommendation_refresh_guard:
            _recommendation_refresh_locks.discard(user_id)


@router.post("/recommendations/regenerate-all")
def regenerate_all_recommendations(force: bool = True, db: Session = Depends(get_db)) -> dict:
    results = []
    profiles = db.query(UserProfile).order_by(UserProfile.id.asc()).all()
    matcher = MatchingService()
    for profile in profiles:
        try:
            recommendations = matcher.generate_for_user(db, profile.id, force=force)
            results.append({"user_id": profile.id, "ok": True, "count": len(recommendations)})
        except RuntimeError as exc:
            logger.exception("regenerate recommendations unavailable user_id=%s", profile.id)
            results.append({"user_id": profile.id, "ok": False, "error": str(exc)})
            break
        except Exception as exc:
            logger.exception("regenerate recommendations failed user_id=%s", profile.id)
            results.append({"user_id": profile.id, "ok": False, "error": str(exc)})
            break
    ok = all(item["ok"] for item in results)
    if not ok:
        raise HTTPException(status_code=503, detail={"message": "批量推荐重建中断", "results": results})
    return {"ok": True, "force": force, "results": results}


@router.get("/recommendations/{user_id}", response_model=list[RecommendationOut])
def list_recommendations(user_id: int, db: Session = Depends(get_db)) -> list[Recommendation]:
    try:
        return (
            db.query(Recommendation)
            .join(Opportunity, Recommendation.opportunity_id == Opportunity.id)
            .options(joinedload(Recommendation.opportunity))
            .filter(Recommendation.user_id == user_id)
            .order_by(Recommendation.total_score.desc())
            .all()
        )
    except Exception as exc:
        logger.exception("list recommendations failed user_id=%s", user_id)
        raise HTTPException(status_code=500, detail="推荐列表读取失败") from exc


@router.get("/tools/weixin/diagnostics")
def weixin_search_diagnostics() -> dict:
    result = WeixinSearchProvider().diagnose()
    return {
        "status": result.status,
        "url": result.url,
        "reachable": result.reachable,
        "tools": result.tools,
        "missing_tools": result.missing_tools,
        "error_message": result.error_message,
        "cache_count": result.cache_count,
        "last_search_status": result.last_search_status,
        "cooldown_until": result.cooldown_until,
        "cooldown_reason": result.cooldown_reason,
    }


@router.get("/tools/weixin/search-test")
def weixin_search_test(query: str = "AI 实习 招募", top_k: int = 3) -> dict:
    result = WeixinSearchProvider().search(query, top_k=top_k)
    return {
        "status": result.status,
        "provider": result.provider,
        "query": result.query,
        "error_message": result.error_message,
        "debug": {
            "provider_status": result.status,
            "raw_item_count": result.raw_item_count,
            "mapped_item_count": result.mapped_item_count,
            "query": query,
            "top_k": top_k,
            "cache_hit": result.cache_hit,
            "cooldown_active": result.cooldown_active,
        },
        "results": [
            {
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet,
                "source": item.source,
                "provider": item.provider,
                "published_at": item.published_at,
                "raw": item.raw,
            }
            for item in result.results
        ],
    }


@router.get("/tools/weixin/raw-search-test")
def weixin_raw_search_test(query: str = "AI 实习 招募", page: int = 1) -> dict:
    safe_page = max(1, min(page, 10))
    summary = WeixinSearchProvider().raw_search_summary(query, page=safe_page)
    return {
        "query": query,
        "page": safe_page,
        **summary,
    }


@router.post("/tools/opportunity-assistant/chat", response_model=OpportunityAssistantChatResponse)
def opportunity_assistant_chat(payload: OpportunityAssistantChatRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return OpportunityAssistantService().chat(
            db=db,
            user_id=payload.user_id,
            opportunity_id=payload.opportunity_id,
            message=payload.message,
            use_web_search=payload.use_web_search,
            include_memory=payload.include_memory,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        logger.exception("opportunity assistant llm failed user_id=%s opportunity_id=%s", payload.user_id, payload.opportunity_id)
        raise HTTPException(status_code=502, detail=f"机会助理生成失败：{exc}") from exc
    except Exception as exc:
        logger.exception("opportunity assistant failed user_id=%s opportunity_id=%s", payload.user_id, payload.opportunity_id)
        raise HTTPException(status_code=500, detail="机会助理暂时不可用") from exc


@router.post("/tools/opportunity-progress/{user_id}/{opportunity_id}/analyze", response_model=OpportunityProgressAnalyzeResponse)
def analyze_opportunity_progress(user_id: int, opportunity_id: int, db: Session = Depends(get_db)) -> dict:
    request_id = str(uuid.uuid4())
    started = time.perf_counter()
    service = OpportunityProgressService()
    try:
        task = service.start_analysis_task(db, user_id, opportunity_id)
        if task.status == "analyzing":
            threading.Thread(target=service.execute_analysis_task, args=(task.id,), daemon=True).start()
        logger.info("opportunity progress task started request_id=%s user_id=%s opportunity_id=%s task_id=%s status=%s elapsed_ms=%s", request_id, user_id, opportunity_id, task.id, task.status, int((time.perf_counter() - started) * 1000))
        return {"success": True, "message": "AI 推进分析已开始。", "task": service.serialize_task(task)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "not_found", "message": str(exc), "request_id": request_id}) from exc
    except RuntimeError as exc:
        logger.exception("opportunity progress analysis failed request_id=%s user_id=%s opportunity_id=%s elapsed_ms=%s", request_id, user_id, opportunity_id, int((time.perf_counter() - started) * 1000))
        raise HTTPException(status_code=502, detail={"error_code": "model_unavailable", "message": "AI 服务暂时不可用", "request_id": request_id}) from exc
    except Exception as exc:
        error_code = "database_busy" if "database is locked" in str(exc).lower() else "unknown_error"
        logger.exception("opportunity progress unexpected failure request_id=%s user_id=%s opportunity_id=%s elapsed_ms=%s error_type=%s", request_id, user_id, opportunity_id, int((time.perf_counter() - started) * 1000), type(exc).__name__)
        raise HTTPException(status_code=503 if error_code == "database_busy" else 500, detail={"error_code": error_code, "message": "系统正在处理其他任务，请稍后重试" if error_code == "database_busy" else "服务处理失败", "request_id": request_id}) from exc


@router.get("/tools/opportunity-progress/tasks/{task_id}", response_model=OpportunityProgressAnalyzeResponse)
def get_opportunity_progress_task(task_id: int, db: Session = Depends(get_db)) -> dict:
    service = OpportunityProgressService()
    task = db.get(OpportunityProgressTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail={"error_code": "not_found", "message": "推进任务不存在", "request_id": str(uuid.uuid4())})
    return {"success": True, "message": "ok", "task": service.serialize_task(task)}


@router.get("/tools/opportunity-progress/{user_id}/{opportunity_id}/task")
def get_latest_opportunity_progress_task(user_id: int, opportunity_id: int, db: Session = Depends(get_db)) -> dict:
    service = OpportunityProgressService()
    task = (
        db.query(OpportunityProgressTask)
        .filter(OpportunityProgressTask.user_id == user_id, OpportunityProgressTask.opportunity_id == opportunity_id)
        .order_by(OpportunityProgressTask.updated_at.desc())
        .first()
    )
    if not task:
        return {"success": True, "message": "暂无推进分析任务", "task": None}
    return {"success": True, "message": "ok", "task": service.serialize_task(task)}


@router.post("/tools/opportunity-progress/tasks/{task_id}/web-check")
def start_task_web_check(task_id: int, db: Session = Depends(get_db)) -> dict:
    request_id = str(uuid.uuid4())
    service = OpportunityProgressService()
    try:
        check = service.start_web_check_for_task(db, task_id, request_id=request_id)
        if check.status == "searching":
            threading.Thread(target=service.execute_web_check, args=(check.id,), daemon=True).start()
        logger.info("progress web check scheduled request_id=%s check_id=%s task_id=%s", request_id, check.id, task_id)
        return {"success": True, "message": "联网核实已开始。", "web_check": service.serialize_web_check(check)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "not_found", "message": str(exc), "request_id": request_id}) from exc
    except Exception as exc:
        logger.exception("start task web check failed request_id=%s task_id=%s error_type=%s", request_id, task_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail={"error_code": "unknown_error", "message": "联网核实启动失败", "request_id": request_id}) from exc


@router.post("/tools/opportunity-progress/plans/{plan_id}/web-check")
def start_plan_web_check(plan_id: int, db: Session = Depends(get_db)) -> dict:
    request_id = str(uuid.uuid4())
    service = OpportunityProgressService()
    try:
        check = service.start_web_check_for_plan(db, plan_id, request_id=request_id)
        if check.status == "searching":
            threading.Thread(target=service.execute_web_check, args=(check.id,), daemon=True).start()
        logger.info("progress web check scheduled request_id=%s check_id=%s plan_id=%s", request_id, check.id, plan_id)
        return {"success": True, "message": "联网核实已开始。", "web_check": service.serialize_web_check(check)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "not_found", "message": str(exc), "request_id": request_id}) from exc
    except Exception as exc:
        logger.exception("start plan web check failed request_id=%s plan_id=%s error_type=%s", request_id, plan_id, type(exc).__name__)
        raise HTTPException(status_code=500, detail={"error_code": "unknown_error", "message": "联网核实启动失败", "request_id": request_id}) from exc


@router.get("/tools/opportunity-progress/web-checks/{check_id}")
def get_progress_web_check(check_id: int, db: Session = Depends(get_db)) -> dict:
    service = OpportunityProgressService()
    try:
        return {"success": True, "web_check": service.serialize_web_check(service.get_web_check(db, check_id))}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail={"error_code": "not_found", "message": str(exc), "request_id": str(uuid.uuid4())}) from exc


@router.get("/tools/opportunity-progress/tasks/{task_id}/web-check")
def get_latest_task_web_check(task_id: int, db: Session = Depends(get_db)) -> dict:
    service = OpportunityProgressService()
    return {"success": True, "web_check": service.serialize_web_check(service.get_latest_web_check(db, task_id=task_id))}


@router.get("/tools/opportunity-progress/plans/{plan_id}/web-check")
def get_latest_plan_web_check(plan_id: int, db: Session = Depends(get_db)) -> dict:
    service = OpportunityProgressService()
    return {"success": True, "web_check": service.serialize_web_check(service.get_latest_web_check(db, plan_id=plan_id))}


@router.post("/tools/opportunity-progress/{task_id}/confirm-todo", response_model=OpportunityProgressConfirmResponse)
def confirm_opportunity_progress_todo(task_id: int, db: Session = Depends(get_db)) -> dict:
    service = OpportunityProgressService()
    try:
        task, todo, message = service.confirm_create_todo(db, task_id)
        return {"success": True, "message": message, "task": service.serialize_task(task), "todo": todo}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("confirm opportunity progress todo failed task_id=%s", task_id)
        raise HTTPException(status_code=500, detail="确认加入待办失败") from exc


@router.post("/tools/opportunity-progress/{task_id}/create-plan")
def create_opportunity_progress_plan(task_id: int, db: Session = Depends(get_db)) -> dict:
    service = OpportunityProgressService()
    try:
        plan = service.create_plan_from_task(db, task_id)
        return {"success": True, "message": "推进计划已建立。", "plan": service.serialize_plan(db, plan)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("create opportunity progress plan failed task_id=%s", task_id)
        raise HTTPException(status_code=500, detail="推进计划建立失败") from exc


@router.get("/tools/opportunity-progress/plans/{plan_id}")
def get_opportunity_progress_plan(plan_id: int, db: Session = Depends(get_db)) -> dict:
    service = OpportunityProgressService()
    try:
        return {"success": True, "plan": service.serialize_plan(db, service.get_plan(db, plan_id))}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/tools/opportunity-progress/{user_id}/{opportunity_id}/plan")
def get_current_opportunity_progress_plan(user_id: int, opportunity_id: int, db: Session = Depends(get_db)) -> dict:
    service = OpportunityProgressService()
    try:
        plan = service.get_current_plan(db, user_id, opportunity_id)
        return {"success": True, "plan": service.serialize_plan(db, plan) if plan else None}
    except Exception as exc:
        logger.exception("get current opportunity progress plan failed user_id=%s opportunity_id=%s", user_id, opportunity_id)
        raise HTTPException(status_code=500, detail="推进计划读取失败") from exc


@router.post("/tools/opportunity-progress/plans/{plan_id}/replan")
def replan_opportunity_progress(
    plan_id: int,
    payload: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
) -> dict:
    service = OpportunityProgressService()
    try:
        update = service.replan(db, plan_id, str(payload.get("progress_text") or ""))
        plan = service.get_plan(db, plan_id)
        return {
            "success": True,
            "message": "AI 已生成计划调整建议，请确认后再更新正式计划。",
            "plan": service.serialize_plan(db, plan),
            "update": service.serialize_update(update),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("replan opportunity progress failed plan_id=%s", plan_id)
        raise HTTPException(status_code=500, detail="重新规划失败") from exc


@router.post("/tools/opportunity-progress/plans/{plan_id}/confirm-update")
def confirm_opportunity_progress_update(
    plan_id: int,
    payload: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
) -> dict:
    service = OpportunityProgressService()
    try:
        update_id = int(payload.get("update_id") or 0)
        plan = service.confirm_plan_update(db, plan_id, update_id)
        return {"success": True, "message": "推进计划已更新。", "plan": service.serialize_plan(db, plan)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("confirm opportunity progress update failed plan_id=%s", plan_id)
        raise HTTPException(status_code=500, detail="确认更新计划失败") from exc


@router.post("/tools/opportunity-progress/plans/{plan_id}/dismiss-update")
def dismiss_opportunity_progress_update(
    plan_id: int,
    payload: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
) -> dict:
    service = OpportunityProgressService()
    try:
        update_id = int(payload.get("update_id") or 0)
        service.dismiss_plan_update(db, plan_id, update_id)
        plan = service.get_plan(db, plan_id)
        return {"success": True, "message": "已保留原计划。", "plan": service.serialize_plan(db, plan)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("dismiss opportunity progress update failed plan_id=%s", plan_id)
        raise HTTPException(status_code=500, detail="暂不修改失败") from exc


@router.post("/tools/opportunity-progress/plans/{plan_id}/sync-todos")
def sync_opportunity_progress_todos(
    plan_id: int,
    payload: dict = Body(default_factory=dict),
    db: Session = Depends(get_db),
) -> dict:
    service = OpportunityProgressService()
    try:
        result = service.sync_plan_items_to_todos(db, plan_id, payload.get("item_ids") or [])
        plan = service.get_plan(db, plan_id)
        return {"success": True, "message": "待办同步完成。", "result": result, "plan": service.serialize_plan(db, plan)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("sync opportunity progress todos failed plan_id=%s", plan_id)
        raise HTTPException(status_code=500, detail="同步待办失败") from exc


@router.post("/tools/opportunity-progress/plans/{plan_id}/complete")
def complete_opportunity_progress_plan(plan_id: int, db: Session = Depends(get_db)) -> dict:
    service = OpportunityProgressService()
    try:
        plan = service.set_plan_status(db, plan_id, "completed")
        return {"success": True, "message": "推进计划已标记为完成。", "plan": service.serialize_plan(db, plan)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tools/opportunity-progress/plans/{plan_id}/pause")
def pause_opportunity_progress_plan(plan_id: int, db: Session = Depends(get_db)) -> dict:
    service = OpportunityProgressService()
    try:
        plan = service.set_plan_status(db, plan_id, "paused")
        return {"success": True, "message": "推进计划已暂停。", "plan": service.serialize_plan(db, plan)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tools/opportunity-progress/plans/{plan_id}/close")
def close_opportunity_progress_plan(plan_id: int, db: Session = Depends(get_db)) -> dict:
    service = OpportunityProgressService()
    try:
        plan = service.set_plan_status(db, plan_id, "cancelled")
        return {"success": True, "message": "推进计划已结束。", "plan": service.serialize_plan(db, plan)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tools/enrich/{user_id}/{opportunity_id}", response_model=EnrichmentResultOut)
def enrich_opportunity_tool(user_id: int, opportunity_id: int, db: Session = Depends(get_db)) -> EnrichmentResult:
    try:
        return SearchEnrichmentService().enrich(db, user_id, opportunity_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        detail = _tool_error_detail(str(exc))
        raise HTTPException(status_code=_tool_status_code(detail["status"]), detail=detail) from exc
    except Exception as exc:
        logger.exception("enrich opportunity tool failed user_id=%s opportunity_id=%s", user_id, opportunity_id)
        raise HTTPException(status_code=500, detail={"status": "error", "error_message": "深挖详情失败"}) from exc


@router.get("/tools/enrich/{user_id}/{opportunity_id}", response_model=EnrichmentResultOut)
def get_enrichment_result(user_id: int, opportunity_id: int, db: Session = Depends(get_db)) -> EnrichmentResult:
    try:
        result = SearchEnrichmentService().get_latest(db, user_id, opportunity_id)
        if not result:
            raise HTTPException(status_code=404, detail="暂无深挖详情结果")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("get enrichment result failed user_id=%s opportunity_id=%s", user_id, opportunity_id)
        raise HTTPException(status_code=500, detail="深挖详情读取失败") from exc


@router.post("/tools/similar/{user_id}/{opportunity_id}", response_model=SimilarOpportunityResultOut)
def search_similar_opportunities_tool(
    user_id: int,
    opportunity_id: int,
    db: Session = Depends(get_db),
) -> SimilarOpportunityResult:
    try:
        return SimilarOpportunitySearchService().search_similar(db, user_id, opportunity_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        detail = _tool_error_detail(str(exc))
        raise HTTPException(status_code=_tool_status_code(detail["status"]), detail=detail) from exc
    except Exception as exc:
        logger.exception("similar opportunity tool failed user_id=%s opportunity_id=%s", user_id, opportunity_id)
        raise HTTPException(status_code=500, detail={"status": "error", "error_message": "查找同类失败"}) from exc


@router.get("/tools/similar/{user_id}/{opportunity_id}", response_model=SimilarOpportunityResultOut)
def get_similar_opportunity_result(
    user_id: int,
    opportunity_id: int,
    db: Session = Depends(get_db),
) -> SimilarOpportunityResult:
    try:
        result = SimilarOpportunitySearchService().get_latest(db, user_id, opportunity_id)
        if not result:
            raise HTTPException(status_code=404, detail="暂无同类机会结果")
        return result
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("get similar opportunity result failed user_id=%s opportunity_id=%s", user_id, opportunity_id)
        raise HTTPException(status_code=500, detail="同类机会结果读取失败") from exc


def _tool_error_detail(message: str) -> dict[str, str]:
    if ":" in message:
        status, error_message = message.split(":", 1)
        return {"status": status.strip(), "error_message": error_message.strip()}
    return {"status": "error", "error_message": message}


def _tool_status_code(status: str) -> int:
    if status == "not_configured":
        return 503
    if status in {"mcp_server_unreachable", "mcp_tool_not_found", "weixin_search_temporarily_limited"}:
        return 503
    if status == "empty_results":
        return 404
    if status == "content_fetch_failed":
        return 502
    if status == "llm_error":
        return 502
    return 502


@router.post("/tools/{tool_name}", response_model=ToolCallOut)
def call_tool(tool_name: str, payload: ToolCallRequest, db: Session = Depends(get_db)):
    try:
        return ToolService().call(db, tool_name, payload.user_id, payload.opportunity_id, payload.payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
