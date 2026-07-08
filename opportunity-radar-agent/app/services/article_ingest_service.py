"""Direct clean-article ingestion service.

This service receives cleaned article text from the GUI collector, writes it
into the existing Article table, and triggers opportunity extraction in the
same database transaction.
"""

from __future__ import annotations

import hashlib
import logging
import re

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.entities import Article
from app.schemas.dto import CleanArticleIngestRequest
from app.services.extraction_service import ExtractionService


logger = logging.getLogger(__name__)


def normalize_for_hash(text: str) -> str:
    """Normalize text only for duplicate detection."""
    return re.sub(r"\s+", "", text or "")


def compute_content_hash(text: str) -> str:
    normalized = normalize_for_hash(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def guess_title_from_content(content: str, fallback: str = "未命名文章") -> str:
    for line in (content or "").splitlines():
        line = line.strip()
        if line:
            return line[:255]
    return fallback


class ArticleIngestService:
    def ingest_clean_article(self, db: Session, payload: CleanArticleIngestRequest) -> dict:
        try:
            return self._ingest_clean_article(db, payload)
        except ValueError:
            db.rollback()
            raise
        except SQLAlchemyError:
            db.rollback()
            logger.exception("ingest_clean_article db failure")
            raise RuntimeError("文章入库失败")
        except Exception:
            db.rollback()
            logger.exception("ingest_clean_article failed")
            raise

    def _ingest_clean_article(self, db: Session, payload: CleanArticleIngestRequest) -> dict:
        content = (payload.content or "").strip()

        if not content:
            raise ValueError("content 不能为空")

        content_hash = payload.content_hash.strip() or compute_content_hash(content)

        existing_articles = db.query(Article).all()
        for article in existing_articles:
            if compute_content_hash(article.content or "") == content_hash:
                logger.info("duplicate clean article article_id=%s title=%s", article.id, article.title)
                return {
                    "created": False,
                    "duplicate": True,
                    "article_id": article.id,
                    "content_hash": content_hash,
                    "message": "duplicate",
                }

        title = (payload.title or "").strip()
        if not title:
            title = guess_title_from_content(content)

        article = Article(
            title=title[:255],
            source=(payload.source or "公众号自动采集")[:120],
            author=(payload.author or "")[:120],
            published_at=payload.published_at or "",
            content=content,
            url=payload.url or "",
            processed=False,
        )

        db.add(article)
        db.flush()
        db.refresh(article)

        created_opportunities = ExtractionService().process_article(db, article, commit=False)
        db.commit()
        db.refresh(article)

        logger.info(
            "clean article ingested article_id=%s title=%s created_opportunities=%s",
            article.id,
            article.title,
            created_opportunities,
        )
        return {
            "created": True,
            "duplicate": False,
            "article_id": article.id,
            "content_hash": content_hash,
            "message": "created",
        }
