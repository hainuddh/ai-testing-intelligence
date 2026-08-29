from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select

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
) -> ContentListResponse:
    filters = []
    if source_id is not None:
        filters.append(ContentItem.source_id == source_id)
    if query is not None:
        pattern = f"%{query}%"
        filters.append(
            or_(
                ContentItem.title.ilike(pattern),
                ContentItem.summary.ilike(pattern),
                ContentItem.body.ilike(pattern),
            )
        )
    total = db.scalar(select(func.count()).select_from(ContentItem).where(*filters)) or 0
    items = list(
        db.scalars(
            select(ContentItem)
            .where(*filters)
            .order_by(ContentItem.fetched_at.desc(), ContentItem.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    return ContentListResponse(items=items, total=total)


@router.get("/{content_id}", response_model=ContentItemResponse)
def get_content(content_id: int, db: DbSession, _user: CurrentUser) -> ContentItem:
    item = db.get(ContentItem, content_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Content item not found")
    return item
