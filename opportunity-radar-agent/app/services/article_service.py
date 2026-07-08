"""Article CRUD service backed by SQLite.

SQLite is the single runtime source of truth. Files may still be used for
ingestion/debug, but API reads and deletes go through this service.
"""

from __future__ import annotations

import logging

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.entities import Article, Opportunity, OpportunityState, Recommendation, Todo, ToolCall, ToolCallLog


logger = logging.getLogger(__name__)


class ArticleService:
    def list_articles(self, db: Session) -> list[Article]:
        try:
            return db.query(Article).order_by(Article.imported_at.desc()).all()
        except SQLAlchemyError:
            logger.exception("list_articles failed")
            raise RuntimeError("文章列表读取失败")

    def delete_article(self, db: Session, article_id: int) -> dict:
        try:
            article = db.get(Article, article_id)
            if not article:
                raise ValueError("文章不存在")

            opportunity_ids = [
                item[0] for item in db.query(Opportunity.id).filter(Opportunity.article_id == article_id).all()
            ]

            deleted_recommendations = 0
            if opportunity_ids:
                deleted_recommendations = (
                    db.query(Recommendation)
                    .filter(Recommendation.opportunity_id.in_(opportunity_ids))
                    .delete(synchronize_session=False)
                )
                db.query(OpportunityState).filter(OpportunityState.opportunity_id.in_(opportunity_ids)).delete(
                    synchronize_session=False
                )
                db.query(Todo).filter(Todo.opportunity_id.in_(opportunity_ids)).delete(synchronize_session=False)
                db.query(ToolCallLog).filter(ToolCallLog.opportunity_id.in_(opportunity_ids)).delete(
                    synchronize_session=False
                )
                db.query(ToolCall).filter(ToolCall.opportunity_id.in_(opportunity_ids)).delete(
                    synchronize_session=False
                )

            deleted_opportunities = (
                db.query(Opportunity).filter(Opportunity.article_id == article_id).delete(synchronize_session=False)
            )
            db.delete(article)
            db.commit()

            logger.info(
                "article deleted article_id=%s opportunities=%s recommendations=%s",
                article_id,
                deleted_opportunities,
                deleted_recommendations,
            )
            return {
                "deleted": True,
                "article_id": article_id,
                "deleted_opportunities": deleted_opportunities,
                "deleted_recommendations": deleted_recommendations,
                "message": "deleted",
            }
        except ValueError:
            db.rollback()
            raise
        except SQLAlchemyError:
            db.rollback()
            logger.exception("delete_article failed article_id=%s", article_id)
            raise RuntimeError("文章删除失败")
