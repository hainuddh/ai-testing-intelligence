from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app import digest_batches as batches
from app import email_digest as mail
from app import worker
from app.fetcher import download_feed, fetch_endpoint
from app.models import DigestBatchEndpoint, FetchRun, Source, SourceEndpoint, User

NOW = datetime(2026, 10, 17, 1, tzinfo=UTC)


def setup_source(db):
    owner = User(username="source-owner", password_hash="unused", role="maintainer")
    db.add(owner)
    db.flush()
    source = Source(name="Testing lab", source_type="rss", created_by=owner.id, status="active")
    db.add(source)
    db.flush()
    endpoint = SourceEndpoint(
        source_id=source.id, name="Feed", endpoint_type="rss", url="https://example.com/feed"
    )
    db.add(endpoint)
    mail.save_recipient(db, email="reader@example.com", now=NOW - timedelta(hours=3))
    mail.update_preferences(
        db, enabled=True, value_threshold=70, send_time="09:00", now=NOW - timedelta(hours=3)
    )
    db.commit()
    return source, endpoint


@pytest.mark.parametrize("state", ["paused", "archived", "endpoint_disabled"])
def test_stop_source_skips_pending_job_and_does_not_block_delivery(db_session, state):
    source, endpoint = setup_source(db_session)
    batch = batches.ensure_batch(db_session, NOW - timedelta(hours=1))
    batch.github_status = "completed"
    if state == "endpoint_disabled":
        endpoint.enabled = False
    else:
        source.status = state
    db_session.flush()
    batches.skip_inactive_source_jobs(db_session, now=NOW)
    db_session.commit()
    job = db_session.scalar(select(DigestBatchEndpoint))
    assert job.status == "skipped"
    assert job.skip_reason
    assert endpoint.id not in [entry.id for entry in worker.due_endpoints(db_session)]
    batches.prepare_batches(db_session, now=NOW)
    assert batch.status == "ready"
    assert db_session.get(Source, source.id) is not None
    assert db_session.get(SourceEndpoint, endpoint.id) is not None


def test_paused_source_is_not_added_to_new_batch(db_session):
    source, endpoint = setup_source(db_session)
    source.status = "paused"
    db_session.commit()
    batches.ensure_batch(db_session, NOW)
    assert db_session.scalar(select(DigestBatchEndpoint)) is None
    assert endpoint.id not in [entry.id for entry in worker.due_endpoints(db_session)]


def test_running_request_can_finish_but_failure_is_not_retried_after_pause(db_session):
    source, endpoint = setup_source(db_session)
    batch = batches.ensure_batch(db_session, NOW - timedelta(hours=1))
    job = db_session.scalar(select(DigestBatchEndpoint))
    run = FetchRun(
        endpoint_id=endpoint.id, status="running", started_at=NOW - timedelta(minutes=15)
    )
    db_session.add(run)
    db_session.flush()
    job.run_id, job.status = run.id, "running"
    source.status = "paused"
    db_session.flush()
    batches.skip_inactive_source_jobs(db_session, now=NOW)
    assert job.status == "running"
    run.status, run.completed_at = "failed", NOW
    db_session.commit()
    batches.refresh_jobs(db_session, batch, NOW)
    db_session.flush()
    db_session.refresh(job)
    assert job.status == "skipped"
    assert job.next_attempt_at is None


def test_collector_rechecks_source_after_work_was_queued(db_session):
    source, endpoint = setup_source(db_session)
    selected = worker.due_endpoints(db_session)
    assert endpoint in selected
    source.status = "paused"
    db_session.commit()
    with patch("app.fetcher.download_feed") as request:
        run = fetch_endpoint(db_session, selected[0])
    request.assert_not_called()
    assert run.status == "skipped"


@pytest.mark.parametrize(
    "document",
    [
        b'<rss version="2.0"><channel><title>Quiet feed</title></channel></rss>',
        b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Quiet feed</title></feed>',
    ],
)
def test_empty_valid_feed_is_healthy_without_trying_repair(document):
    with patch(
        "app.fetcher.download",
        return_value=("https://example.com/feed", document, "application/xml"),
    ) as request:
        url, items = download_feed("https://example.com/feed", 5)
    assert url == "https://example.com/feed" and items == []
    request.assert_called_once()


def test_unchanged_web_page_keeps_existing_analysis_and_collection_time(db_session):
    from app.models import ContentItem

    _source, endpoint = setup_source(db_session)
    endpoint.endpoint_type = "web"
    db_session.commit()
    page = b"<html><head><title>Testing</title></head><body>Stable content</body></html>"
    with patch("app.fetcher.download", return_value=(endpoint.url, page, "text/html")):
        fetch_endpoint(db_session, endpoint)
        item = db_session.scalar(select(ContentItem))
        item.analysis_status = "analyzed"
        item.fetched_at = NOW - timedelta(days=1)
        item.analyzed_at = NOW
        db_session.commit()
        saved_time = item.fetched_at
        run = fetch_endpoint(db_session, endpoint)
    assert item.analysis_status == "analyzed"
    assert item.fetched_at == saved_time
    assert run.items_created == 0
    assert run.content_ids == []
