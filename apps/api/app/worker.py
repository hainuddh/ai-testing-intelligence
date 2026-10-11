import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.analyzer import analyze_pending, enrich_pending_related_links
from app.cache import delete_prefix_sync
from app.config import settings
from app.database import engine
from app.digest_batches import (
    analysis_priority_ids,
    prepare_batches,
    required_endpoint_ids,
    run_batch_research,
)
from app.email_digest import (
    configured_digest_schedule,
    github_research_pending,
    needs_daily_preparation,
    send_queued_digest,
)
from app.fetcher import fetch_endpoint
from app.github_service import run_github_exploration
from app.models import (
    DigestBatch,
    DigestBatchEndpoint,
    EmailDigestRecipient,
    FetchRun,
    GitHubExplorationState,
    SourceEndpoint,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def due_endpoints(db: Session) -> list[SourceEndpoint]:
    now = datetime.now(UTC)
    required_ids = required_endpoint_ids(db, now)
    last_run = (
        select(FetchRun.endpoint_id, func.max(FetchRun.started_at).label("last_started_at"))
        .group_by(FetchRun.endpoint_id)
        .subquery()
    )
    endpoints = db.execute(
        select(SourceEndpoint, last_run.c.last_started_at)
        .outerjoin(last_run, SourceEndpoint.id == last_run.c.endpoint_id)
        .where(SourceEndpoint.enabled | SourceEndpoint.id.in_(required_ids))
    ).all()
    daily_preparation = needs_daily_preparation(db, now)
    schedule = configured_digest_schedule(db, now)
    return [
        endpoint
        for endpoint, last_started_at in endpoints
        if last_started_at is None
        or endpoint.id in required_ids
        or (
            daily_preparation
            and last_started_at.replace(tzinfo=last_started_at.tzinfo or UTC) < schedule.prepare_at
        )
        or last_started_at.replace(tzinfo=last_started_at.tzinfo or UTC)
        <= now - timedelta(minutes=endpoint.fetch_interval_minutes)
    ]


def run_analysis() -> None:
    if engine.dialect.name != "postgresql":
        with Session(engine) as db:
            analyzed, failed = analyze_pending(db, priority_ids=analysis_priority_ids(db))
            links_enriched = enrich_pending_related_links(db)
    else:
        with engine.connect() as lock_connection:
            if not lock_connection.scalar(text("SELECT pg_try_advisory_lock(82429102)")):
                return
            try:
                with Session(engine) as db:
                    analyzed, failed = analyze_pending(db, priority_ids=analysis_priority_ids(db))
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
    """Run discovery, analysis and publication when the persistent schedule is due."""
    try:
        with Session(engine) as db:
            now = datetime.now(UTC)
            previous = db.get(GitHubExplorationState, 1)
            schedule = configured_digest_schedule(db, now)
            overdue_research = db.scalar(
                select(DigestBatch.id).where(DigestBatch.github_status != "completed").limit(1)
            )
            if schedule is not None and (now >= schedule.prepare_at or overdue_research):
                run_batch_research(db, now)
                return
            if schedule is not None:
                # Align existing 24h schedules and preference changes before work starts.
                if previous is None:
                    previous = GitHubExplorationState(id=1, status="never_run")
                    db.add(previous)
                if previous.status in {"never_run", "completed"}:
                    next_run_at = (
                        schedule.prepare_at
                        if now < schedule.prepare_at or github_research_pending(db, now)
                        else schedule.prepare_at + timedelta(days=1)
                    )
                    db.execute(
                        update(GitHubExplorationState)
                        .where(
                            GitHubExplorationState.id == 1,
                            GitHubExplorationState.status.in_(["never_run", "completed"]),
                            GitHubExplorationState.lease_token.is_(None),
                        )
                        .values(next_run_at=next_run_at)
                    )
                    db.commit()
                if now < schedule.prepare_at or not github_research_pending(db, now):
                    return
            force = (
                needs_daily_preparation(db, now)
                and github_research_pending(db, now)
                and previous is not None
                and previous.status == "completed"
            )
            state = run_github_exploration(
                db,
                force=force,
                report_date=schedule.business_date if schedule is not None else None,
            )
            if state.status == "failed":
                logger.warning("GitHub exploration will retry at %s", state.next_run_at)
    except Exception:
        logger.exception("GitHub discovery failed")


def run_email_digest() -> None:
    """Compatibility queue check; production delivery runs in app.mail_sender."""
    try:
        with Session(engine) as db:
            ids = list(
                db.scalars(select(EmailDigestRecipient.id).where(EmailDigestRecipient.enabled))
            )
            for recipient_id in ids:
                send_queued_digest(db, recipient_id)
    except Exception:
        # SMTP failures are recorded with sanitized messages by the delivery service.
        logger.error("Email digest task failed; will check again on the next worker poll")


def run_digest_preparation() -> None:
    try:
        with Session(engine) as db:
            prepare_batches(db)
    except Exception:
        logger.exception("Daily batch preparation will resume on the next poll")


def run_collection_cycle() -> None:
    with Session(engine) as db:
        any_created = False
        for endpoint in due_endpoints(db):
            try:
                job = db.scalar(
                    select(DigestBatchEndpoint)
                    .where(
                        DigestBatchEndpoint.endpoint_id == endpoint.id,
                        DigestBatchEndpoint.status.in_(["pending", "failed"]),
                        (
                            DigestBatchEndpoint.next_attempt_at.is_(None)
                            | (DigestBatchEndpoint.next_attempt_at <= datetime.now(UTC))
                        ),
                    )
                    .order_by(DigestBatchEndpoint.batch_id)
                    .limit(1)
                )
                run = (
                    fetch_endpoint(db, endpoint, batch_job_id=job.id)
                    if job
                    else (fetch_endpoint(db, endpoint))
                )
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
    if any_created:
        delete_prefix_sync("content:list")
        delete_prefix_sync("collected:list")


def run_once() -> None:
    if engine.dialect.name != "postgresql":
        run_collection_cycle()
        run_analysis()
        return
    with engine.connect() as lock_connection:
        if not lock_connection.scalar(text("SELECT pg_try_advisory_lock(82429101)")):
            return
        try:
            # Serialize source collection; GitHub and mail have their own durable leases.
            run_collection_cycle()
        finally:
            lock_connection.execute(text("SELECT pg_advisory_unlock(82429101)"))
    # Release collection's connection before analysis acquires its own lock.
    run_analysis()


def main() -> None:
    logger.info("Collection worker started poll_seconds=%s", settings.worker_poll_seconds)
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="digest-preparation") as executor:
        futures = {}
        while True:
            for name, task in (("collection", run_once), ("github", run_github_discovery)):
                previous = futures.get(name)
                if previous is None or previous.done():
                    if previous is not None:
                        try:
                            previous.result()
                        except Exception:
                            logger.exception(
                                "%s preparation failed; will retry on the next poll", name
                            )
                    futures[name] = executor.submit(task)
            # Coordinate durable batches without network I/O; SMTP is a separate process.
            run_digest_preparation()
            # Rejoin clock-minute boundaries so HH:MM delivery does not drift with work duration.
            time.sleep(min(max(settings.worker_poll_seconds, 1), 60 - time.time() % 60))


if __name__ == "__main__":
    main()
