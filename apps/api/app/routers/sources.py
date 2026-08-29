from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.dependencies import CurrentUser, DbSession, MaintainerUser
from app.models import Source, SourceEndpoint
from app.schemas import (
    EndpointCreate,
    EndpointResponse,
    SourceCreate,
    SourceListResponse,
    SourceResponse,
)

router = APIRouter(prefix="/sources", tags=["sources"])


@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
def create_source(payload: SourceCreate, db: DbSession, user: MaintainerUser) -> Source:
    source = Source(
        name=payload.name,
        source_type=payload.source_type,
        homepage_url=str(payload.homepage_url) if payload.homepage_url else None,
        description=payload.description,
        languages=payload.languages,
        trust_level=payload.trust_level,
        topics=payload.topics,
        created_by=user.id,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    return source


@router.get("", response_model=SourceListResponse)
def list_sources(
    db: DbSession,
    _user: CurrentUser,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> SourceListResponse:
    total = db.scalar(select(func.count()).select_from(Source)) or 0
    items = list(
        db.scalars(select(Source).order_by(Source.created_at.desc()).offset(offset).limit(limit))
    )
    return SourceListResponse(items=items, total=total)


@router.post(
    "/{source_id}/endpoints",
    response_model=EndpointResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_endpoint(
    source_id: int,
    payload: EndpointCreate,
    db: DbSession,
    _user: MaintainerUser,
) -> SourceEndpoint:
    if db.get(Source, source_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    endpoint = SourceEndpoint(
        source_id=source_id,
        name=payload.name,
        endpoint_type=payload.endpoint_type,
        url=str(payload.url),
        fetch_interval_minutes=payload.fetch_interval_minutes,
        max_items_per_run=payload.max_items_per_run,
    )
    db.add(endpoint)
    db.commit()
    db.refresh(endpoint)
    return endpoint
