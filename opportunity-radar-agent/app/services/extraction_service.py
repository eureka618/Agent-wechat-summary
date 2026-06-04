from sqlalchemy.orm import Session

from app.models.entities import Article, Opportunity
from app.services.json_utils import dumps
from app.services.llm_service import CATEGORIES, LLMService


class ExtractionService:
    def __init__(self) -> None:
        self.llm = LLMService()

    def process_unprocessed_articles(self, db: Session) -> tuple[int, int]:
        articles = db.query(Article).filter(Article.processed.is_(False)).all()
        created = 0
        for article in articles:
            extracted = self.llm.extract_opportunities(article.title, article.content)
            for item in extracted:
                opportunity = self._to_opportunity(article.id, item)
                db.add(opportunity)
                created += 1
            article.processed = True
        db.commit()
        return len(articles), created

    def _to_opportunity(self, article_id: int, item: dict) -> Opportunity:
        category = item.get("category") or "其他"
        if category not in CATEGORIES:
            category = "其他"
        return Opportunity(
            article_id=article_id,
            name=str(item.get("name") or "未命名机会")[:255],
            category=category,
            organizer=str(item.get("organizer") or ""),
            event_time=str(item.get("event_time") or ""),
            deadline=str(item.get("deadline") or ""),
            location=str(item.get("location") or ""),
            target_audience=str(item.get("target_audience") or ""),
            link=str(item.get("link") or ""),
            requirements=str(item.get("requirements") or ""),
            summary=str(item.get("summary") or ""),
            benefit=float(item.get("benefit") or 0.6),
            cost=float(item.get("cost") or 0.4),
            credibility=float(item.get("credibility") or 0.7),
            raw_payload=dumps(item),
        )
