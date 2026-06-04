from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import UserProfile
from app.services.matching_service import MatchingService
from app.services.summary_service import SummaryService


scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def scheduled_summary_job() -> None:
    db = SessionLocal()
    try:
        users = db.query(UserProfile).all()
        matcher = MatchingService()
        summarizer = SummaryService()
        for user in users:
            matcher.generate_for_user(db, user.id)
            summarizer.generate(db, user.id, "daily")
    finally:
        db.close()


def start_scheduler() -> None:
    settings = get_settings()
    if scheduler.running:
        return
    scheduler.add_job(
        scheduled_summary_job,
        "cron",
        hour=settings.summary_cron_hour,
        minute=settings.summary_cron_minute,
        id="daily_summary",
        replace_existing=True,
    )
    scheduler.start()


def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown()
