from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.config import settings
from app.routers import auth, collected_content, content, database_status, sources, users


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
api = APIRouter(prefix="/api/v1")


@api.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


api.include_router(auth.router)
api.include_router(sources.router)
api.include_router(content.router)
api.include_router(collected_content.router)
api.include_router(users.router)
api.include_router(database_status.router)
app.include_router(api)
