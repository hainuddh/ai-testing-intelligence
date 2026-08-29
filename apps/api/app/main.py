from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from app.config import settings
from app.database import Base, engine
from app.routers import auth, sources


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
api = APIRouter(prefix="/api/v1")


@api.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    return {"status": "ok"}


api.include_router(auth.router)
api.include_router(sources.router)
app.include_router(api)
