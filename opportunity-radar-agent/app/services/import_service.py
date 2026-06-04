import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.models.entities import Article
from app.schemas.dto import ArticleCreate


class ImportService:
    def import_file(self, db: Session, path: str) -> list[Article]:
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在：{path}")
        suffix = file_path.suffix.lower()
        if suffix == ".json":
            payloads = self._load_json(file_path)
        elif suffix in {".md", ".markdown", ".txt"}:
            payloads = [self._load_text(file_path)]
        else:
            raise ValueError("仅支持 JSON / Markdown / TXT 文件")

        articles: list[Article] = []
        for payload in payloads:
            article_in = ArticleCreate(**payload)
            article = Article(**article_in.model_dump())
            db.add(article)
            articles.append(article)
        db.commit()
        for article in articles:
            db.refresh(article)
        return articles

    def _load_json(self, file_path: Path) -> list[dict]:
        data = json.loads(file_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "articles" in data:
            data = data["articles"]
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            raise ValueError("JSON 格式应为文章对象、文章数组，或包含 articles 的对象")
        return [self._normalize_article(item, file_path.stem) for item in data]

    def _load_text(self, file_path: Path) -> dict:
        content = file_path.read_text(encoding="utf-8")
        title = file_path.stem
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        if file_path.suffix.lower() in {".md", ".markdown"} and lines and lines[0].startswith("#"):
            title = lines[0].lstrip("#").strip() or title
        return {
            "title": title,
            "source": "本地导入",
            "author": "",
            "published_at": "",
            "content": content,
            "url": "",
        }

    def _normalize_article(self, item: dict, fallback_title: str) -> dict:
        return {
            "title": item.get("title") or fallback_title,
            "source": item.get("source") or item.get("account") or "本地导入",
            "author": item.get("author") or "",
            "published_at": item.get("published_at") or item.get("date") or "",
            "content": item.get("content") or item.get("text") or "",
            "url": item.get("url") or item.get("link") or "",
        }
