from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import defer, selectinload

from app.cache import cache_key, delete_prefix_sync, get_json, set_json
from app.config import settings
from app.dependencies import AdminUser, AdminUserAsync, AsyncDbSession, DbSession
from app.models import ContentItem
from app.schemas import (
    CollectedContentListResponse,
    ContentBulkDeleteRequest,
    ContentBulkDeleteResponse,
)

router = APIRouter(prefix="/collected-content", tags=["collected-content"])
AnalysisStatus = Literal["pending", "analyzed", "filtered", "failed"]


def wait_for_content_workers(db: DbSession) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(82429101)"))
        db.execute(text("SELECT pg_advisory_xact_lock(82429102)"))


@router.get("", response_model=CollectedContentListResponse)
async def list_collected_content(
    db: AsyncDbSession,
    _admin: AdminUserAsync,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    query: str | None = Query(default=None, min_length=1, max_length=200),
    analysis_status: Annotated[AnalysisStatus | None, Query(alias="status")] = None,
    source_id: int | None = Query(default=None, ge=1),
    start_at: Annotated[datetime | None, Query()] = None,
    end_at: Annotated[datetime | None, Query()] = None,
) -> CollectedContentListResponse:
    if start_at is not None and end_at is not None and start_at > end_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="start_at must be before or equal to end_at",
        )
    filters = []
    if query is not None:
        escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped_query}%"
        filters.append(
            or_(
                ContentItem.title.ilike(pattern, escape="\\"),
                ContentItem.summary.ilike(pattern, escape="\\"),
            )
        )
    if analysis_status is not None:
        filters.append(ContentItem.analysis_status == analysis_status)
    if source_id is not None:
        filters.append(ContentItem.source_id == source_id)
    if start_at is not None:
        filters.append(ContentItem.fetched_at >= start_at)
    if end_at is not None:
        filters.append(ContentItem.fetched_at <= end_at)
    key = cache_key(
        "collected:list",
        offset=offset,
        limit=limit,
        query=query,
        status=analysis_status,
        source_id=source_id,
        start_at=start_at,
        end_at=end_at,
    )
    cached = await get_json(key)
    if cached is not None:
        return CollectedContentListResponse.model_validate(cached)
    rows = await db.execute(
        select(ContentItem, func.count().over().label("total"))
        .options(selectinload(ContentItem.source), defer(ContentItem.body))
        .where(*filters)
        .order_by(ContentItem.fetched_at.desc(), ContentItem.id.desc())
        .offset(offset)
        .limit(limit)
    )
    result = rows.all()
    items = [row[0] for row in result]
    total = result[0][1] if result else 0
    response = CollectedContentListResponse(items=items, total=total)
    await set_json(key, response.model_dump(mode="json"), settings.content_cache_ttl)
    return response


@router.delete("/{content_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_collected_content(
    content_id: int, db: DbSession, _admin: AdminUser
) -> None:
    wait_for_content_workers(db)
    item = db.get(ContentItem, content_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")
    db.delete(item)
    db.commit()
    delete_prefix_sync("content:list")
    delete_prefix_sync("content:item")
    delete_prefix_sync("collected:list")


@router.post("/bulk-delete", response_model=ContentBulkDeleteResponse)
def bulk_delete_collected_content(
    payload: ContentBulkDeleteRequest, db: DbSession, _admin: AdminUser
) -> ContentBulkDeleteResponse:
    wait_for_content_workers(db)
    content_ids = list(dict.fromkeys(payload.content_ids))
    items = list(db.scalars(select(ContentItem).where(ContentItem.id.in_(content_ids))))
    if len(items) != len(content_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more content items do not exist",
        )
    for item in items:
        db.delete(item)
    db.commit()
    delete_prefix_sync("content:list")
    delete_prefix_sync("content:item")
    delete_prefix_sync("collected:list")
    return ContentBulkDeleteResponse(deleted=len(items))
