from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.dependencies import CurrentUser, DbSession
from app.models import ContentItem
from app.reporting import render_markdown_report
from app.schemas import ContentExportRequest, ContentItemResponse, ContentListResponse

router = APIRouter(prefix="/content", tags=["content"])


@router.post("/export")
def export_content(
    payload: ContentExportRequest, db: DbSession, _user: CurrentUser
) -> Response:
    content_ids = list(dict.fromkeys(payload.content_ids))
    items = list(
        db.scalars(
            select(ContentItem)
            .options(selectinload(ContentItem.source))
            .where(
                ContentItem.id.in_(content_ids),
                ContentItem.analysis_status == "analyzed",
                ContentItem.testing_relevance_score >= settings.testing_relevance_threshold,
            )
        )
    )
    items_by_id = {item.id: item for item in items}
    if any(content_id not in items_by_id for content_id in content_ids):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="One or more content items are unavailable for export",
        )
    ordered_items = [items_by_id[content_id] for content_id in content_ids]
    filename = f"testing-intelligence-report-{datetime.now().strftime('%Y%m%d')}.md"
    return Response(
        content=render_markdown_report(ordered_items),
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


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
        escaped_query = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped_query}%"
        filters.append(
            or_(
                ContentItem.title.ilike(pattern, escape="\\"),
                ContentItem.summary.ilike(pattern, escape="\\"),
                ContentItem.body.ilike(pattern, escape="\\"),
                ContentItem.analysis_summary.ilike(pattern, escape="\\"),
                ContentItem.testing_value_analysis.ilike(pattern, escape="\\"),
            )
        )
    effective_at = func.coalesce(ContentItem.published_at, ContentItem.fetched_at)
    if start_at is not None:
        filters.append(effective_at >= start_at)
    if end_at is not None:
        filters.append(effective_at <= end_at)
    rows = db.execute(
        select(ContentItem, func.count().over().label("total"))
        .options(selectinload(ContentItem.source))
        .where(*filters)
        .order_by(
            ContentItem.testing_value_score.desc(),
            ContentItem.fetched_at.desc(),
            ContentItem.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    ).all()
    items = [row[0] for row in rows]
    total = rows[0][1] if rows else 0
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
