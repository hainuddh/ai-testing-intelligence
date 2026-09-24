import logging
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.analyzer import analyze_pending, enrich_pending_related_links
from app.cache import delete_prefix_sync
from app.config import settings
from app.database import engine
from app.fetcher import fetch_endpoint
from app.github_service import discover_candidates, discovery_due
from app.models import FetchRun, SourceEndpoint

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def due_endpoints(db: Session) -> list[SourceEndpoint]:
    last_run = (
        select(FetchRun.endpoint_id, func.max(FetchRun.started_at).label("last_started_at"))
        .group_by(FetchRun.endpoint_id)
        .subquery()
    )
    endpoints = db.execute(
        select(SourceEndpoint, last_run.c.last_started_at)
        .outerjoin(last_run, SourceEndpoint.id == last_run.c.endpoint_id)
        .where(SourceEndpoint.enabled)
    ).all()
    now = datetime.now(UTC)
    return [
        endpoint
        for endpoint, last_started_at in endpoints
        if last_started_at is None
        or last_started_at.replace(tzinfo=last_started_at.tzinfo or UTC)
        <= now - timedelta(minutes=endpoint.fetch_interval_minutes)
    ]


def run_analysis() -> None:
    if engine.dialect.name != "postgresql":
        with Session(engine) as db:
            analyzed, failed = analyze_pending(db)
            links_enriched = enrich_pending_related_links(db)
    else:
        with engine.connect() as lock_connection:
            if not lock_connection.scalar(text("SELECT pg_try_advisory_lock(82429102)")):
                return
            try:
                with Session(engine) as db:
                    analyzed, failed = analyze_pending(db)
                    links_enriched = enrich_pending_related_links(db)
            finally:
                lock_connection.execute(text("SELECT pg_advisory_unlock(82429102)"))
    if analyzed or failed:
        logger.info("Analyzed content succeeded=%s failed=%s", analyzed, failed)
    if links_enriched:
        logger.info("Enriched related links items=%s", links_enriched)
    if analyzed or links_enriched:
        delete_prefix_sync("content:list")
        delete_prefix_sync("content:item")
        delete_prefix_sync("collected:list")


def run_github_discovery() -> None:
    """定时触发 GitHub 自动发现（无 token 时受 Search API 配额约束）。"""
    try:
        with Session(engine) as db:
            if not discovery_due(db):
                return
            count = discover_candidates(db, settings.github_discovery_languages_list)
            if count:
                logger.info("GitHub discovery discovered=%s", count)
    except Exception:
        logger.exception("GitHub discovery failed")


def run_once() -> None:
    if engine.dialect.name != "postgresql":
        with Session(engine) as db:
            any_created = False
            for endpoint in due_endpoints(db):
                try:
                    run = fetch_endpoint(db, endpoint)
                    if run.items_created > 0:
                        any_created = True
                    logger.info(
                        "Fetched endpoint=%s status=%s items_created=%s",
                        endpoint.name,
                        run.status,
                        run.items_created,
                    )
                except Exception:
                    db.rollback()
                    logger.exception("Unhandled fetch failure endpoint_id=%s", endpoint.id)
        run_analysis()
        run_github_discovery()
        if any_created:
            delete_prefix_sync("content:list")
            delete_prefix_sync("collected:list")
        return

    with engine.connect() as lock_connection:
        if not lock_connection.scalar(text("SELECT pg_try_advisory_lock(82429101)")):
            return
        try:
            with Session(engine) as db:
                any_created = False
                for endpoint in due_endpoints(db):
                    try:
                        run = fetch_endpoint(db, endpoint)
                        if run.items_created > 0:
                            any_created = True
                        logger.info(
                            "Fetched endpoint=%s status=%s items_created=%s",
                            endpoint.name,
                            run.status,
                            run.items_created,
                        )
                    except Exception:
                        db.rollback()
                        logger.exception("Unhandled fetch failure endpoint_id=%s", endpoint.id)
        finally:
            lock_connection.execute(text("SELECT pg_advisory_unlock(82429101)"))
    run_analysis()
    run_github_discovery()
    if any_created:
        delete_prefix_sync("content:list")
        delete_prefix_sync("collected:list")


def main() -> None:
    logger.info("Collection worker started poll_seconds=%s", settings.worker_poll_seconds)
    while True:
        run_once()
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
