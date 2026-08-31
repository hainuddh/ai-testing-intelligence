from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.dependencies import AdminUser, CurrentUser, DbSession, MaintainerUser
from app.models import Source, SourceEndpoint
from app.schemas import (
    EndpointCreate,
    EndpointResponse,
    SourceCreate,
    SourceListResponse,
    SourceResponse,
    SourceUpdate,
)

router = APIRouter(prefix="/sources", tags=["sources"])


def wait_for_content_workers(db: DbSession) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(82429101)"))
        db.execute(text("SELECT pg_advisory_xact_lock(82429102)"))


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
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Source name already exists"
        ) from exc
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


@router.patch("/{source_id}", response_model=SourceResponse)
def update_source(
    source_id: int,
    payload: SourceUpdate,
    db: DbSession,
    _user: MaintainerUser,
) -> Source:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    changes = payload.model_dump(exclude_unset=True)
    required_fields = {"name", "source_type", "languages", "trust_level", "topics"}
    if any(changes.get(field) is None for field in required_fields & changes.keys()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Required source fields cannot be null",
        )
    if "homepage_url" in changes and changes["homepage_url"] is not None:
        changes["homepage_url"] = str(changes["homepage_url"])
    for field, value in changes.items():
        setattr(source, field, value)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Source name already exists"
        ) from exc
    db.refresh(source)
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, db: DbSession, _user: AdminUser) -> None:
    wait_for_content_workers(db)
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    db.delete(source)
    db.commit()


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


@router.get("/{source_id}/endpoints", response_model=list[EndpointResponse])
def list_endpoints(
    source_id: int, db: DbSession, _user: CurrentUser
) -> list[SourceEndpoint]:
    if db.get(Source, source_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return list(
        db.scalars(
            select(SourceEndpoint)
            .where(SourceEndpoint.source_id == source_id)
            .order_by(SourceEndpoint.created_at.desc())
        )
    )


@router.delete("/{source_id}/endpoints/{endpoint_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_endpoint(
    source_id: int,
    endpoint_id: int,
    db: DbSession,
    _user: MaintainerUser,
) -> None:
    wait_for_content_workers(db)
    endpoint = db.scalar(
        select(SourceEndpoint).where(
            SourceEndpoint.id == endpoint_id, SourceEndpoint.source_id == source_id
        )
    )
    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Endpoint not found")
    db.delete(endpoint)
    db.commit()
