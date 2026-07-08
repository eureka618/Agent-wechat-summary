from pathlib import Path

from app.core.database import SessionLocal, init_db
from app.models.entities import Article, UserProfile
from app.services.import_service import ImportService
from app.services.json_utils import dumps
from app.services.extraction_service import ExtractionService
from app.services.matching_service import MatchingService


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        profile = db.query(UserProfile).filter(UserProfile.name == "演示用户").first()
        if not profile:
            profile = UserProfile(
                name="演示用户",
                major_direction="人工智能",
                grade_identity="大三本科生",
                current_goals=dumps(["科研入门", "找实习", "保研"]),
                skills=dumps(["Python", "机器学习", "PyTorch", "SQL"]),
                interested_fields=dumps(["AI Agent", "多模态", "数据分析", "RAG"]),
                disliked_contents=dumps(["纯营销", "无证书训练营"]),
                detailed_needs="希望找到对保研和科研经历有帮助的 AI Agent / RAG 相关机会，最好能产出项目、论文复现或导师推荐信；实习方向偏数据分析和机器学习，地点优先线上、北京、上海、深圳。",
                time_preference="暑期 周末 晚上",
                location_preference="线上 北京 上海 深圳",
            )
            db.add(profile)
            db.commit()
            db.refresh(profile)

        sample_path = Path(__file__).resolve().parents[1] / "data" / "sample_articles.json"
        if db.query(Article).count() == 0:
            ImportService().import_file(db, str(sample_path))
        ExtractionService().process_unprocessed_articles(db)
        recommendations = MatchingService().generate_for_user(db, profile.id)

        print("Seed 完成")
        print(f"用户 ID: {profile.id}")
        print(f"推荐数: {len(recommendations)}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
