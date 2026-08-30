from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.dependencies import CurrentUser, DbSession
from app.models import ContentItem
from app.schemas import ContentItemResponse, ContentListResponse

router = APIRouter(prefix="/content", tags=["content"])


@router.get("", response_model=ContentListResponse)
def list_content(
    db: DbSession,
    _user: CurrentUser,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    source_id: int | None = Query(default=None, ge=1),
    query: str | None = Query(default=None, min_length=1, max_length=200),
    start_at: Annotated[datetime | None, Query()] = None,
    end_at: Annotated[datetime | None, Query()] = None,
    min_value_score: int = Query(default=60, ge=0, le=100),
) -> ContentListResponse:
    if start_at is not None and end_at is not None and start_at > end_at:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="start_at must be before or equal to end_at",
        )
    filters = [
        ContentItem.analysis_status == "analyzed",
        ContentItem.testing_relevance_score >= settings.testing_relevance_threshold,
        ContentItem.testing_value_score >= min_value_score,
    ]
    if source_id is not None:
        filters.append(ContentItem.source_id == source_id)
    if query is not None:
        pattern = f"%{query}%"
        filters.append(
            or_(
                ContentItem.title.ilike(pattern),
                ContentItem.summary.ilike(pattern),
                ContentItem.body.ilike(pattern),
                ContentItem.analysis_summary.ilike(pattern),
                ContentItem.testing_value_analysis.ilike(pattern),
            )
        )
    effective_at = func.coalesce(ContentItem.published_at, ContentItem.fetched_at)
    if start_at is not None:
        filters.append(effective_at >= start_at)
    if end_at is not None:
        filters.append(effective_at <= end_at)
    total = db.scalar(select(func.count()).select_from(ContentItem).where(*filters)) or 0
    items = list(
        db.scalars(
            select(ContentItem)
            .options(selectinload(ContentItem.source))
            .where(*filters)
            .order_by(
                ContentItem.testing_value_score.desc(),
                ContentItem.fetched_at.desc(),
                ContentItem.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
    )
    return ContentListResponse(items=items, total=total)


@router.get("/{content_id}", response_model=ContentItemResponse)
def get_content(content_id: int, db: DbSession, _user: CurrentUser) -> ContentItem:
    item = db.get(ContentItem, content_id)
    if (
        item is None
        or item.analysis_status != "analyzed"
        or (item.testing_relevance_score or 0) < settings.testing_relevance_threshold
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")
    return item
