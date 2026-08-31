from fastapi import APIRouter
from sqlalchemy import func, select

from app.cache import cache_key, get_json, set_json
from app.config import settings
from app.dependencies import AdminUserAsync, AsyncDbSession
from app.models import ContentItem, FetchRun, Source, SourceEndpoint, User
from app.schemas import DatabaseStatusResponse

router = APIRouter(prefix="/database", tags=["database"])


@router.get("/status", response_model=DatabaseStatusResponse)
async def database_status(
    db: AsyncDbSession, _admin: AdminUserAsync
) -> DatabaseStatusResponse:
    key = cache_key("database:status")
    cached = await get_json(key)
    if cached is not None:
        return DatabaseStatusResponse.model_validate(cached)
    models = {
        "users": User,
        "sources": Source,
        "source_endpoints": SourceEndpoint,
        "content_items": ContentItem,
        "fetch_runs": FetchRun,
    }
    row_counts = {}
    for name, model in models.items():
        row_counts[name] = await db.scalar(select(func.count()).select_from(model)) or 0
    response = DatabaseStatusResponse(
        dialect=db.get_bind().dialect.name, row_counts=row_counts
    )
    await set_json(key, response.model_dump(mode="json"), settings.database_cache_ttl)
    return response
