import os
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

# 部署时由 deploy-run.sh 注入产物目录；拷贝安装布局下 __file__ 推导不可靠
web_dist = os.environ.get("ATI_WEB_DIST", "")

if web_dist and Path(web_dist).is_dir():
    web_dist = Path(web_dist).resolve()
    app.mount("/assets", StaticFiles(directory=web_dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str) -> FileResponse:
        candidate = (web_dist / full_path).resolve()
        if candidate.is_file() and candidate.is_relative_to(web_dist):
            return FileResponse(candidate)
        return FileResponse(web_dist / "index.html")
