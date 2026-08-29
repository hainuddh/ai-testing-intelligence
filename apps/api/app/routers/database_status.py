from fastapi import APIRouter
from sqlalchemy import func, select

from app.dependencies import AdminUser, DbSession
from app.models import ContentItem, FetchRun, Source, SourceEndpoint, User
from app.schemas import DatabaseStatusResponse

router = APIRouter(prefix="/database", tags=["database"])


@router.get("/status", response_model=DatabaseStatusResponse)
def database_status(db: DbSession, _admin: AdminUser) -> DatabaseStatusResponse:
    models = {
        "users": User,
        "sources": Source,
        "source_endpoints": SourceEndpoint,
        "content_items": ContentItem,
        "fetch_runs": FetchRun,
    }
    row_counts = {
        name: db.scalar(select(func.count()).select_from(model)) or 0
        for name, model in models.items()
    }
    return DatabaseStatusResponse(dialect=db.get_bind().dialect.name, row_counts=row_counts)
