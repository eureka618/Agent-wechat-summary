from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.agent_actions import router as agent_actions_router
from app.api.growth_memory import router as growth_memory_router
from app.api.routes import router
from app.core.config import get_settings
from app.core.database import init_db
from app.core.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.include_router(router)
app.include_router(agent_actions_router)
app.include_router(growth_memory_router)
