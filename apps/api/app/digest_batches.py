"""Durable calendar batches. Slow work completes its batch after the cutoff."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.digest_schedule import digest_schedule
from app.models import (
    ContentItem,
    DigestBatch,
    DigestBatchEndpoint,
    EmailDigestDelivery,
    EmailDigestItem,
    EmailDigestPreference,
    EmailDigestRecipient,
    FetchRun,
    GitHubExplorationState,
    GitHubReport,
    SourceEndpoint,
)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC)


def ensure_batch(db: Session, now: datetime) -> DigestBatch | None:
    preference = db.get(EmailDigestPreference, 1)
    if (
        not preference
        or not preference.enabled
        or db.scalar(select(EmailDigestRecipient.id).where(EmailDigestRecipient.enabled).limit(1))
        is None
    ):
        return None
    schedule = digest_schedule(preference.send_time, now)
    batch = db.scalar(
        select(DigestBatch).where(DigestBatch.business_date == str(schedule.business_date))
    )
    if batch is not None or utc(now) < schedule.prepare_at:
        return batch
    previous_cutoff = db.scalar(
        select(DigestBatch.cutoff_at)
        .where(
            DigestBatch.business_date < str(schedule.business_date),
        )
        .order_by(DigestBatch.business_date.desc())
        .limit(1)
    )
    batch = DigestBatch(
        business_date=str(schedule.business_date),
        window_start=utc(previous_cutoff)
        if previous_cutoff
        else (schedule.cutoff_at - timedelta(days=1)),
        prepare_at=schedule.prepare_at,
        cutoff_at=schedule.cutoff_at,
        send_at=schedule.send_at,
    )
    db.add(batch)
    try:
        db.flush()
        db.add_all(
            [
                DigestBatchEndpoint(batch_id=batch.id, endpoint_id=endpoint_id)
                for endpoint_id in db.scalars(
                    select(SourceEndpoint.id).where(SourceEndpoint.enabled)
                )
            ]
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        batch = db.scalar(
            select(DigestBatch).where(DigestBatch.business_date == str(schedule.business_date))
        )
    return batch


def jobs_for(db: Session, batch: DigestBatch) -> list[DigestBatchEndpoint]:
    return list(
        db.scalars(
            select(DigestBatchEndpoint)
            .where(DigestBatchEndpoint.batch_id == batch.id)
            .execution_options(populate_existing=True)
        )
    )


def refresh_jobs(db: Session, batch: DigestBatch, now: datetime) -> None:
    for job in jobs_for(db, batch):
        previous_run_id = job.run_id
        run = db.get(FetchRun, job.run_id) if job.run_id else None
        latest = db.scalar(
            select(FetchRun)
            .where(
                FetchRun.endpoint_id == job.endpoint_id,
                FetchRun.started_at >= batch.prepare_at,
                FetchRun.started_at <= batch.cutoff_at,
            )
            .order_by(FetchRun.started_at.desc(), FetchRun.id.desc())
            .limit(1)
        )
        if latest and (
            run is None or (utc(run.started_at) <= utc(batch.cutoff_at) and latest.id >= run.id)
        ):
            run = latest
        if run is None:
            continue
        retry_at = (
            utc(run.completed_at) + timedelta(minutes=5)
            if run.status == "failed" and run.completed_at
            else None
        )
        status = run.status
        if run.status == "running" and utc(run.started_at) <= utc(now) - timedelta(hours=1):
            status, retry_at = "failed", now
        db.execute(
            update(DigestBatchEndpoint)
            .where(
                DigestBatchEndpoint.id == job.id,
                DigestBatchEndpoint.run_id == previous_run_id,
            )
            .values(run_id=run.id, status=status, next_attempt_at=retry_at)
            .execution_options(synchronize_session=False)
        )


def capture_research_completion(db: Session, batch: DigestBatch, now: datetime) -> None:
    if batch.github_status == "completed":
        return
    state = db.get(GitHubExplorationState, 1, populate_existing=True)
    if not (
        state
        and state.status == "completed"
        and state.started_at
        and state.finished_at
        and utc(batch.prepare_at) <= utc(state.started_at) <= utc(state.finished_at) <= utc(now)
    ):
        return
    report = db.get(GitHubReport, state.report_id) if state.report_id else None
    matching_report = (
        report
        and report.status == "ready"
        and (
            report.report_key == f"daily:{batch.business_date}"
            or (report.report_key is None and utc(report.generated_at) <= utc(batch.send_at))
        )
    )
    own_empty = not state.report_id and (
        (batch.github_started_at and utc(state.started_at) >= utc(batch.github_started_at))
        or utc(state.started_at) <= utc(batch.cutoff_at)
    )
    if matching_report or own_empty:
        batch.github_status = "completed"
        batch.github_report_id = state.report_id


def refresh_batch(db: Session, batch: DigestBatch, now: datetime) -> None:
    if batch.status == "ready":
        return
    required = set(batch.required_content_ids)
    refresh_jobs(db, batch, now)
    for job in jobs_for(db, batch):
        run = db.get(FetchRun, job.run_id) if job.run_id else None
        if run is None:
            continue
        required.update(run.content_ids or [])
    # Ordinary collections started before the cutoff also belong to this batch.
    for ids in db.scalars(
        select(FetchRun.content_ids).where(
            FetchRun.started_at >= batch.window_start,
            FetchRun.started_at <= batch.cutoff_at,
            FetchRun.status == "succeeded",
            FetchRun.completed_at <= now,
        )
    ):
        required.update(ids or [])
    if not batch.sealed:
        required.update(
            db.scalars(
                select(ContentItem.id).where(
                    ContentItem.fetched_at >= batch.window_start,
                    ContentItem.fetched_at <= min(utc(now), utc(batch.cutoff_at)),
                )
            )
        )
        if utc(now) >= utc(batch.cutoff_at):
            batch.sealed = True
            first_start = (
                db.scalar(
                    select(EmailDigestRecipient.started_at)
                    .where(
                        EmailDigestRecipient.enabled,
                        EmailDigestRecipient.started_at.is_not(None),
                    )
                    .order_by(EmailDigestRecipient.started_at)
                    .limit(1)
                )
                or batch.prepare_at
            )
            reserved = (
                select(EmailDigestItem.id)
                .join(EmailDigestDelivery)
                .where(
                    func.lower(EmailDigestDelivery.recipient) == EmailDigestRecipient.email,
                    EmailDigestItem.content_hash == ContentItem.url_hash,
                )
                .correlate(EmailDigestRecipient, ContentItem)
                .exists()
            )
            unserved = (
                select(EmailDigestRecipient.id)
                .where(
                    EmailDigestRecipient.enabled,
                    EmailDigestRecipient.started_at <= ContentItem.analyzed_at,
                    ~reserved,
                )
                .correlate(ContentItem)
                .exists()
            )
            preference = db.get(EmailDigestPreference, 1)
            batch.candidate_content_ids = list(
                db.scalars(
                    select(ContentItem.id).where(
                        ContentItem.analysis_status == "analyzed",
                        ContentItem.analyzed_at >= first_start,
                        ContentItem.analyzed_at <= batch.cutoff_at,
                        or_(
                            ContentItem.analysis_disposition == "radar",
                            ContentItem.analysis_disposition.is_(None),
                        ),
                        ContentItem.testing_value_score > preference.value_threshold,
                        ContentItem.testing_relevance_score >= settings.testing_relevance_threshold,
                        unserved,
                    )
                )
            )
            batch.report_ids = list(
                db.scalars(
                    select(GitHubReport.id).where(
                        GitHubReport.report_type == "daily",
                        GitHubReport.status == "ready",
                        GitHubReport.generated_at >= first_start,
                        GitHubReport.generated_at <= batch.cutoff_at,
                    )
                )
            )
    batch.required_content_ids = sorted(required)
    # Recover publication completed immediately before a worker restart. Once captured,
    # subsequent singleton exploration runs cannot invalidate this batch's completion.
    capture_research_completion(db, batch, now)
    pending_sources = sum(job.status != "succeeded" for job in jobs_for(db, batch))
    pending_analysis = db.scalar(
        select(ContentItem.id)
        .where(ContentItem.id.in_(required), ContentItem.analysis_status != "analyzed")
        .limit(1)
    )
    inflight = db.scalar(
        select(FetchRun.id)
        .where(
            FetchRun.started_at >= batch.window_start,
            FetchRun.started_at <= batch.cutoff_at,
            FetchRun.started_at > now - timedelta(hours=1),
            FetchRun.status == "running",
        )
        .limit(1)
    )
    reason = (
        f"等待{pending_sources}个信源采集完成，失败任务每5分钟重试"
        if pending_sources
        else "等待截稿前开始的采集完成"
        if inflight is not None
        else "等待本批内容分析完成或失败重试"
        if pending_analysis is not None
        else "等待本批GitHub研究完成或失败重试"
        if batch.github_status != "completed"
        else None
    )
    batch.error_message = reason
    batch.status = "delayed" if reason and utc(now) >= utc(batch.send_at) else "preparing"
    db.commit()


def required_endpoint_ids(db: Session, now: datetime) -> list[int]:
    ensure_batch(db, now)
    ids = set()
    for batch in db.scalars(select(DigestBatch).where(DigestBatch.status != "ready")):
        refresh_jobs(db, batch, now)
        db.commit()
        for job in jobs_for(db, batch):
            # Recover a collection killed mid-request without launching duplicate work.
            if job.status in {"pending", "failed"}:
                if job.next_attempt_at is None or utc(job.next_attempt_at) <= utc(now):
                    ids.add(job.endpoint_id)
    return sorted(ids)


def analysis_priority_ids(db: Session) -> list[int]:
    ids = set()
    for values in db.scalars(
        select(DigestBatch.required_content_ids).where(DigestBatch.status != "ready")
    ):
        ids.update(values)
    return sorted(ids)


def run_batch_research(db: Session, now: datetime) -> bool:
    """Return whether daily mail owns research scheduling, including overdue batches."""
    from app.github_service import run_github_exploration

    ensure_batch(db, now)
    preference = db.get(EmailDigestPreference, 1)
    if (
        not preference
        or not preference.enabled
        or db.scalar(select(EmailDigestRecipient.id).where(EmailDigestRecipient.enabled).limit(1))
        is None
    ):
        return False
    for batch in db.scalars(
        select(DigestBatch)
        .where(DigestBatch.github_status != "completed")
        .order_by(DigestBatch.business_date)
    ):
        capture_research_completion(db, batch, now)
        db.commit()
        if batch.github_status == "completed":
            continue
        previous = db.get(GitHubExplorationState, 1, populate_existing=True)
        if (
            previous
            and previous.status == "running"
            and previous.started_at
            and utc(previous.started_at) > utc(now) - timedelta(hours=1)
        ):
            return True
        if (
            previous
            and previous.status == "failed"
            and previous.next_run_at
            and (utc(previous.next_run_at) > utc(now))
        ):
            return True
        batch.github_status = "running"
        batch.github_started_at = now
        db.commit()
        state = run_github_exploration(
            db,
            force=previous is None or previous.status in {"completed", "never_run"},
            report_date=date.fromisoformat(batch.business_date),
        )
        if state.status == "completed":
            capture_research_completion(db, batch, datetime.now(UTC))
        elif state.status == "failed":
            batch.github_status = "failed"
        db.commit()
        return True
    return True


def prepare_batches(db: Session, *, now: datetime | None = None) -> list[EmailDigestDelivery]:
    from app.email_digest import run_recipient_digest

    now = utc(now or datetime.now(UTC))
    if ensure_batch(db, now) is None:
        preference = db.get(EmailDigestPreference, 1)
        if not preference or not preference.enabled:
            return []
    deliveries = []
    preference = db.get(EmailDigestPreference, 1)
    today = str(digest_schedule(preference.send_time, now).business_date)
    batch_ids = list(
        db.scalars(
            select(DigestBatch.id)
            .where(
                or_(DigestBatch.status != "ready", DigestBatch.business_date == today),
            )
            .order_by(DigestBatch.business_date)
        )
    )
    for batch_id in batch_ids:
        token = str(uuid4())
        claimed = db.execute(
            update(DigestBatch)
            .where(
                DigestBatch.id == batch_id,
                or_(DigestBatch.lease_until.is_(None), DigestBatch.lease_until <= now),
            )
            .values(lease_token=token, lease_until=now + timedelta(minutes=10))
        )
        db.commit()
        if claimed.rowcount != 1:
            continue
        try:
            batch = db.get(DigestBatch, batch_id, populate_existing=True)
            refresh_batch(db, batch, now)
            if now < utc(batch.cutoff_at) or batch.error_message:
                continue
            batch.candidate_content_ids = sorted(
                set(batch.candidate_content_ids).union(batch.required_content_ids)
            )
            if batch.github_report_id:
                batch.report_ids = sorted(set(batch.report_ids).union([batch.github_report_id]))
            db.commit()
            for recipient_id in list(
                db.scalars(
                    select(EmailDigestRecipient.id).where(
                        EmailDigestRecipient.enabled,
                        EmailDigestRecipient.started_at <= batch.cutoff_at,
                    )
                )
            ):
                delivery = run_recipient_digest(db, recipient_id, now, batch=batch)
                if delivery is not None:
                    deliveries.append(delivery)
            batch.status = "ready"
            batch.completed_at = batch.completed_at or now
            db.commit()
        finally:
            db.rollback()
            db.execute(
                update(DigestBatch)
                .where(
                    DigestBatch.id == batch_id,
                    DigestBatch.lease_token == token,
                )
                .values(lease_token=None, lease_until=None)
            )
            db.commit()
    return deliveries


def batch_progress(db: Session, batch: DigestBatch) -> dict:
    jobs = jobs_for(db, batch)
    pending = (
        []
        if batch.status == "ready"
        else list(
            db.scalars(
                select(ContentItem).where(
                    ContentItem.id.in_(batch.required_content_ids),
                    ContentItem.analysis_status != "analyzed",
                )
            )
        )
    )
    retries = [utc(job.next_attempt_at) for job in jobs if job.next_attempt_at]
    retries += [utc(item.next_analysis_at) for item in pending if item.next_analysis_at]
    state = db.get(GitHubExplorationState, 1)
    if batch.github_status != "completed" and state and state.next_run_at:
        retries.append(utc(state.next_run_at))
    return dict(
        id=batch.id,
        business_date=batch.business_date,
        window_start=batch.window_start,
        status=batch.status,
        prepare_at=batch.prepare_at,
        cutoff_at=batch.cutoff_at,
        send_at=batch.send_at,
        completed_at=batch.completed_at,
        error_message=batch.error_message,
        sources_total=len(jobs),
        sources_completed=sum(job.status == "succeeded" for job in jobs),
        analysis_total=len(batch.required_content_ids),
        analysis_pending=len(pending),
        github_status=batch.github_status,
        next_attempt_at=min(retries) if retries else None,
    )
