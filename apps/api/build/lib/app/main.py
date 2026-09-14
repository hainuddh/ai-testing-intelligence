from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        candidate = (WEB_DIST / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(WEB_DIST):
            return FileResponse(candidate)
        return FileResponse(WEB_DIST / "index.html")
