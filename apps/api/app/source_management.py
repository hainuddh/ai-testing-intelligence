"""Source lifecycle and evidence-based management read models."""

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ContentItem, FetchRun, Source, SourceEndpoint
from app.schemas import SourceListResponse, SourceMetrics, SourceResponse, SourceSummary


def normalize_feed_url(url: str) -> str:
    """Normalize equivalent addresses without merging distinct paths or query feeds."""
    parts = urlsplit(url.strip())
    hostname = (parts.hostname or "").lower().encode("idna").decode("ascii")
    if ":" in hostname:
        hostname = f"[{hostname}]"
    port = parts.port
    if port and (parts.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
        hostname += f":{port}"
    if "@" in parts.netloc:
        hostname = parts.netloc.rsplit("@", 1)[0] + "@" + hostname
    return urlunsplit((parts.scheme.lower(), hostname, parts.path or "/", parts.query, ""))


def ensure_feed_available(db: Session, url: str, exclude_endpoint_id: int | None = None) -> None:
    # Every official endpoint writer uses the same lock, including trial approval.
    # It serializes check+insert on PostgreSQL without rewriting existing duplicate feeds.
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(82429103)"))
    query = select(SourceEndpoint.id, SourceEndpoint.url)
    if exclude_endpoint_id is not None:
        query = query.where(SourceEndpoint.id != exclude_endpoint_id)
    normalized = normalize_feed_url(url)
    for _endpoint_id, existing_url in db.execute(query):
        if normalize_feed_url(existing_url) == normalized:
            raise HTTPException(
                status_code=409,
                detail="此订阅已存在，请管理已有信源，避免重复添加。",
            )


def listening_status(db: Session, source: Source) -> str:
    enabled = db.scalar(
        select(SourceEndpoint.id)
        .where(
            SourceEndpoint.source_id == source.id,
            SourceEndpoint.enabled.is_(True),
        )
        .limit(1)
    )
    return "active" if enabled is not None else "draft"


def change_lifecycle(db: Session, source: Source, action: str) -> None:
    if action == "pause":
        if source.status == "paused":
            return
        if source.status == "archived":
            raise HTTPException(status_code=409, detail="请先恢复已归档的信源。")
        source.previous_status = source.status
        source.status = "paused"
    elif action == "resume":
        if source.status == "archived":
            raise HTTPException(status_code=409, detail="请先恢复已归档的信源。")
        source.status = listening_status(db, source)
    elif action == "archive":
        if source.status == "archived":
            return
        source.previous_status = source.status
        source.status = "archived"
    elif action == "restore":
        if source.status != "archived":
            raise HTTPException(status_code=409, detail="只有已归档的信源可以执行恢复操作。")
        source.status = (
            "paused" if source.previous_status == "paused" else listening_status(db, source)
        )


def sync_paused_jobs(db: Session) -> None:
    from app.digest_batches import skip_inactive_source_jobs

    db.flush()
    skip_inactive_source_jobs(db)


async def source_health(db: AsyncSession, sources: list[Source]) -> dict[int, SourceMetrics]:
    metrics = {source.id: SourceMetrics() for source in sources}
    if not sources:
        return metrics
    terminal = (
        select(
            FetchRun.endpoint_id,
            FetchRun.status,
            FetchRun.error,
            FetchRun.started_at,
            func.row_number()
            .over(
                partition_by=FetchRun.endpoint_id,
                order_by=(FetchRun.started_at.desc(), FetchRun.id.desc()),
            )
            .label("rank"),
        )
        .where(FetchRun.status.in_(["succeeded", "failed"]))
        .subquery()
    )
    successes = (
        select(
            FetchRun.endpoint_id,
            func.max(FetchRun.completed_at).label("last_success_at"),
        )
        .where(FetchRun.status == "succeeded")
        .group_by(FetchRun.endpoint_id)
        .subquery()
    )
    rows = await db.execute(
        select(
            SourceEndpoint.source_id,
            SourceEndpoint.enabled,
            terminal.c.status,
            terminal.c.error,
            terminal.c.started_at,
            successes.c.last_success_at,
        )
        .outerjoin(
            terminal,
            and_(terminal.c.endpoint_id == SourceEndpoint.id, terminal.c.rank == 1),
        )
        .outerjoin(successes, successes.c.endpoint_id == SourceEndpoint.id)
    )
    latest_errors: dict[int, datetime] = {}
    for source_id, enabled, run_status, error, started_at, last_success_at in rows:
        if source_id not in metrics:
            continue
        item = metrics[source_id]
        item.endpoint_count += 1
        if last_success_at is not None and (
            item.last_success_at is None or last_success_at > item.last_success_at
        ):
            item.last_success_at = last_success_at
        if not enabled:
            continue
        item.enabled_endpoint_count += 1
        item.healthy_endpoint_count += int(run_status == "succeeded")
        item.unhealthy_endpoint_count += int(run_status == "failed")
        if run_status == "failed" and (
            source_id not in latest_errors or started_at > latest_errors[source_id]
        ):
            latest_errors[source_id] = started_at
            item.last_error = (error or "Collection failed")[:500]
    for source in sources:
        item = metrics[source.id]
        if item.unhealthy_endpoint_count:
            item.health_status = (
                "unhealthy"
                if item.unhealthy_endpoint_count == item.enabled_endpoint_count
                else "partial"
            )
        elif item.healthy_endpoint_count:
            item.health_status = (
                "healthy"
                if item.healthy_endpoint_count == item.enabled_endpoint_count
                else "partial"
            )
        item.needs_attention = source.status in {"active", "draft"} and (
            (item.enabled_endpoint_count == 0 and source.source_type not in {"wechat", "weibo"})
            or item.unhealthy_endpoint_count > 0
        )
    return metrics


async def add_content_metrics(
    db: AsyncSession,
    metrics: dict[int, SourceMetrics],
    source_ids: list[int],
) -> None:
    if not source_ids:
        return
    now = datetime.now(UTC)
    filters = [
        ContentItem.source_id.in_(source_ids),
        ContentItem.fetched_at >= now - timedelta(days=30),
        ContentItem.fetched_at <= now,
    ]
    analyzed = ContentItem.analysis_status == "analyzed"
    radar = and_(
        analyzed,
        or_(
            ContentItem.analysis_disposition == "radar", ContentItem.analysis_disposition.is_(None)
        ),
        ContentItem.testing_relevance_score >= settings.testing_relevance_threshold,
    )
    rows = await db.execute(
        select(
            ContentItem.source_id,
            func.count().label("collected_count"),
            func.sum(case((analyzed, 1), else_=0)).label("analyzed_count"),
            func.sum(case((radar, 1), else_=0)).label("radar_count"),
            func.sum(case((ContentItem.analysis_status == "pending", 1), else_=0)).label(
                "pending_count"
            ),
            func.sum(case((ContentItem.analysis_status == "failed", 1), else_=0)).label(
                "failed_count"
            ),
        )
        .where(*filters)
        .group_by(ContentItem.source_id)
    )
    for row in rows.mappings():
        item = metrics[row["source_id"]]
        for field in (
            "collected_count",
            "analyzed_count",
            "radar_count",
            "pending_count",
            "failed_count",
        ):
            setattr(item, field, row[field])
    samples = (
        select(
            ContentItem.source_id,
            ContentItem.id,
            ContentItem.title,
            ContentItem.url,
            func.row_number()
            .over(
                partition_by=ContentItem.source_id,
                order_by=(ContentItem.fetched_at.desc(), ContentItem.id.desc()),
            )
            .label("rank"),
        )
        .where(*filters)
        .subquery()
    )
    from app.schemas import SourceContentSample

    rows = await db.execute(select(samples).where(samples.c.rank <= 3).order_by(samples.c.rank))
    for row in rows.mappings():
        metrics[row["source_id"]].sample_items.append(
            SourceContentSample(
                id=row["id"],
                title=row["title"],
                url=row["url"],
            )
        )


async def list_managed_sources(
    db: AsyncSession,
    *,
    offset: int,
    limit: int,
    q: str | None = None,
    topic: str | None = None,
    language: str | None = None,
    status: str | None = None,
    needs_attention: bool | None = None,
    exclude_archived: bool = False,
) -> SourceListResponse:
    # Source metadata is small; filter exact JSON memberships in Python consistently
    # on SQLite and PostgreSQL. Content rows are aggregated only for this page.
    sources = list(
        (
            await db.scalars(
                select(Source).order_by(
                    Source.created_at.desc(),
                    Source.id.desc(),
                )
            )
        ).all()
    )
    health = await source_health(db, sources)
    summary = SourceSummary(total=len(sources))
    for source in sources:
        if source.status in {"active", "paused", "archived", "draft"}:
            setattr(summary, source.status, getattr(summary, source.status) + 1)
        summary.needs_attention += int(health[source.id].needs_attention)
    query = (q or "").strip().casefold()
    filtered = [
        source
        for source in sources
        if (
            (
                not query
                or query in source.name.casefold()
                or query in (source.homepage_url or "").casefold()
            )
            and (not topic or topic in source.topics)
            and (not language or language in source.languages)
            and (not status or source.status == status)
            and (not exclude_archived or source.status != "archived")
            and (needs_attention is None or health[source.id].needs_attention == needs_attention)
        )
    ]
    page = filtered[offset : offset + limit]
    await add_content_metrics(db, health, [source.id for source in page])
    items = [
        SourceResponse.model_validate(source).model_copy(
            update={
                "metrics": health[source.id],
                "health_status": health[source.id].health_status,
            }
        )
        for source in page
    ]
    return SourceListResponse(items=items, total=len(filtered), summary=summary)
