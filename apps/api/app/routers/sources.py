from typing import Literal
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.cache import cache_key, delete_prefix_sync, get_json, set_json
from app.config import settings
from app.dependencies import (
    AdminUser,
    AsyncDbSession,
    CurrentUserAsync,
    DbSession,
    MaintainerUser,
)
from app.fetcher import download_feed
from app.models import Source, SourceEndpoint
from app.schemas import (
    EndpointCreate,
    EndpointResponse,
    EndpointUpdate,
    SourceCreate,
    SourceDiscoveryInstall,
    SourceDiscoveryInstallResponse,
    SourceDiscoveryRequest,
    SourceDiscoveryResponse,
    SourceLifecycleRequest,
    SourceListResponse,
    SourceResponse,
    SourceUpdate,
)
from app.source_management import (
    change_lifecycle,
    ensure_feed_available,
    list_managed_sources,
    sync_paused_jobs,
)

router = APIRouter(prefix="/sources", tags=["sources"])


def wait_for_content_workers(db: DbSession) -> None:
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(82429101)"))
        db.execute(text("SELECT pg_advisory_xact_lock(82429102)"))


@router.post("/discover", response_model=SourceDiscoveryResponse)
def discover_source(
    payload: SourceDiscoveryRequest, _user: MaintainerUser
) -> SourceDiscoveryResponse:
    homepage_url = str(payload.homepage_url)
    try:
        feed_url, items = download_feed(homepage_url, 5, homepage_url)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="No valid RSS or Atom feed was found for this website",
        ) from exc
    hostname = urlparse(homepage_url).hostname or "Discovered source"
    return SourceDiscoveryResponse(
        homepage_url=homepage_url,
        feed_url=feed_url,
        suggested_name=hostname.removeprefix("www."),
        samples=items,
    )


@router.post(
    "/discover/install",
    response_model=SourceDiscoveryInstallResponse,
    status_code=status.HTTP_201_CREATED,
)
def install_discovered_source(
    payload: SourceDiscoveryInstall, db: DbSession, user: MaintainerUser
) -> SourceDiscoveryInstallResponse:
    homepage_url = str(payload.homepage_url)
    feed_url = str(payload.feed_url)
    try:
        verified_url, _items = download_feed(feed_url, 3, homepage_url)
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="The discovered feed is no longer valid",
        ) from exc
    ensure_feed_available(db, feed_url)
    ensure_feed_available(db, verified_url)
    source = Source(
        name=payload.name,
        source_type="website",
        homepage_url=homepage_url,
        languages=payload.languages,
        trust_level=payload.trust_level,
        topics=payload.topics,
        status="active",
        created_by=user.id,
    )
    endpoint = SourceEndpoint(
        source=source,
        name="Auto-discovered RSS/Atom",
        endpoint_type="rss",
        url=verified_url,
        fetch_interval_minutes=360,
        max_items_per_run=50,
        health_status="unknown",
    )
    db.add_all([source, endpoint])
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Source name already exists"
        ) from exc
    db.refresh(source)
    db.refresh(endpoint)
    delete_prefix_sync("sources:list")
    delete_prefix_sync("sources:endpoints")
    return SourceDiscoveryInstallResponse(source=source, endpoint=endpoint)


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
    delete_prefix_sync("sources:list")
    return source


@router.get("", response_model=SourceListResponse)
async def list_sources(
    db: AsyncDbSession,
    _user: CurrentUserAsync,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    q: str | None = Query(default=None, max_length=200),
    topic: str | None = Query(default=None, max_length=200),
    language: str | None = Query(default=None, max_length=50),
    status: Literal["active", "paused", "archived", "draft"] | None = None,
    needs_attention: bool | None = None,
    exclude_archived: bool = False,
) -> SourceListResponse:
    key = cache_key(
        "sources:list",
        version=2,
        offset=offset,
        limit=limit,
        q=q,
        topic=topic,
        language=language,
        status=status,
        needs_attention=needs_attention,
        exclude_archived=exclude_archived,
    )
    cached = await get_json(key)
    if cached is not None:
        return SourceListResponse.model_validate(cached)
    response = await list_managed_sources(
        db,
        offset=offset,
        limit=limit,
        q=q,
        topic=topic,
        language=language,
        status=status,
        needs_attention=needs_attention,
        exclude_archived=exclude_archived,
    )
    await set_json(key, response.model_dump(mode="json"), settings.sources_cache_ttl)
    return response


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
    required_fields = {"name", "source_type", "languages", "trust_level", "topics", "status"}
    if any(changes.get(field) is None for field in required_fields & changes.keys()):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Required source fields cannot be null",
        )
    if "homepage_url" in changes and changes["homepage_url"] is not None:
        changes["homepage_url"] = str(changes["homepage_url"])
    if "status" in changes:
        requested_status = changes.pop("status")
        if requested_status == "archived":
            change_lifecycle(db, source, "archive")
        elif requested_status == "paused":
            change_lifecycle(db, source, "pause")
        elif source.status == "archived":
            raise HTTPException(status_code=409, detail="Restore the archived source first")
        elif requested_status == "active":
            change_lifecycle(db, source, "resume")
        else:
            source.status = "draft"
    for field, value in changes.items():
        setattr(source, field, value)
    try:
        if source.status in {"paused", "archived"}:
            sync_paused_jobs(db)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Source name already exists"
        ) from exc
    db.refresh(source)
    delete_prefix_sync("sources:list")
    return source


@router.post("/{source_id}/lifecycle", response_model=SourceResponse)
def update_source_lifecycle(
    source_id: int,
    payload: SourceLifecycleRequest,
    db: DbSession,
    _user: MaintainerUser,
) -> Source:
    source = db.scalar(select(Source).where(Source.id == source_id).with_for_update())
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    change_lifecycle(db, source, payload.action)
    sync_paused_jobs(db)
    db.commit()
    db.refresh(source)
    delete_prefix_sync("sources:list")
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, db: DbSession, _user: AdminUser) -> None:
    wait_for_content_workers(db)
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    db.delete(source)
    db.commit()
    delete_prefix_sync("sources:list")
    delete_prefix_sync("content:list")
    delete_prefix_sync("content:item")
    delete_prefix_sync("collected:list")


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
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    if source.source_type in {"wechat", "weibo"} and payload.endpoint_type != "rss":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="WeChat and Weibo sources only support RSS/Atom endpoints",
        )
    ensure_feed_available(db, str(payload.url))
    endpoint = SourceEndpoint(
        source_id=source_id,
        name=payload.name,
        endpoint_type=payload.endpoint_type,
        url=str(payload.url),
        fetch_interval_minutes=payload.fetch_interval_minutes,
        max_items_per_run=payload.max_items_per_run,
    )
    db.add(endpoint)
    if source.status == "draft":
        source.status = "active"
    db.commit()
    db.refresh(endpoint)
    delete_prefix_sync("sources:endpoints")
    delete_prefix_sync("sources:list")
    return endpoint


@router.patch("/{source_id}/endpoints/{endpoint_id}", response_model=EndpointResponse)
def update_endpoint(
    source_id: int,
    endpoint_id: int,
    payload: EndpointUpdate,
    db: DbSession,
    _user: MaintainerUser,
) -> SourceEndpoint:
    endpoint = db.scalar(
        select(SourceEndpoint).where(
            SourceEndpoint.id == endpoint_id,
            SourceEndpoint.source_id == source_id,
        )
    )
    if endpoint is None:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    source = db.get(Source, source_id)
    changes = payload.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        raise HTTPException(status_code=422, detail="Endpoint fields cannot be null")
    if (
        source.source_type in {"wechat", "weibo"}
        and changes.get(
            "endpoint_type",
            endpoint.endpoint_type,
        )
        != "rss"
    ):
        raise HTTPException(
            status_code=422, detail="WeChat and Weibo sources only support RSS/Atom endpoints"
        )
    if "url" in changes:
        changes["url"] = str(changes["url"])
        ensure_feed_available(db, changes["url"], exclude_endpoint_id=endpoint_id)
    for field, value in changes.items():
        setattr(endpoint, field, value)
    if source.status == "draft" and endpoint.enabled:
        source.status = "active"
    sync_paused_jobs(db)
    db.commit()
    db.refresh(endpoint)
    delete_prefix_sync("sources:endpoints")
    delete_prefix_sync("sources:list")
    return endpoint


@router.get("/{source_id}/endpoints", response_model=list[EndpointResponse])
async def list_endpoints(
    source_id: int, db: AsyncDbSession, _user: CurrentUserAsync
) -> list[SourceEndpoint]:
    key = cache_key("sources:endpoints", source_id=source_id)
    cached = await get_json(key)
    if cached is not None:
        return [EndpointResponse.model_validate(item) for item in cached]
    if await db.get(Source, source_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    items = list(
        (
            await db.scalars(
                select(SourceEndpoint)
                .where(SourceEndpoint.source_id == source_id)
                .order_by(SourceEndpoint.created_at.desc())
            )
        ).all()
    )
    await set_json(
        key,
        [EndpointResponse.model_validate(item).model_dump(mode="json") for item in items],
        settings.sources_cache_ttl,
    )
    return items


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
    delete_prefix_sync("sources:endpoints")
    delete_prefix_sync("sources:list")
