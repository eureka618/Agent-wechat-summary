from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.agent_actions import router as agent_actions_router
from app.api.growth_memory import router as growth_memory_router
from app.api.routes import router
from app.core.config import get_settings
from app.core.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


settings = get_settings()
app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
logger = logging.getLogger(__name__)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled api error path=%s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "服务内部错误，请稍后重试"})


app.include_router(router)
app.include_router(agent_actions_router)
app.include_router(growth_memory_router)
