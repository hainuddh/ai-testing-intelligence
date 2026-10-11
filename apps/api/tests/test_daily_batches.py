from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app import digest_batches as batches
from app import email_digest as mail
from app.models import (
    ContentItem,
    DigestBatch,
    DigestBatchEndpoint,
    FetchRun,
    Source,
    SourceEndpoint,
    User,
)

SEND = datetime(2026, 10, 11, 1, tzinfo=UTC)
PREPARE = SEND - timedelta(hours=2)
CUTOFF = SEND - timedelta(minutes=10)


@pytest.fixture
def daily(db_session, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "radar@example.com")
    mail.save_recipient(db_session, email="reader@example.com")
    mail.update_preferences(
        db_session,
        enabled=True,
        value_threshold=70,
        send_time="09:00",
        now=PREPARE - timedelta(days=1),
    )
    owner = User(username="owner", password_hash="unused", role="admin")
    db_session.add(owner)
    db_session.flush()
    source = Source(name="Testing", source_type="blog", created_by=owner.id)
    db_session.add(source)
    db_session.commit()
    return source


def article(db, source, name, *, fetched=PREPARE, analyzed=CUTOFF, status="analyzed"):
    obj = ContentItem(
        source_id=source.id,
        title=name,
        url=f"https://example.com/{name}",
        fetched_at=fetched,
        analyzed_at=analyzed,
        analysis_status=status,
        analysis_disposition="radar",
        testing_relevance_score=80,
        testing_value_score=90,
        analysis_summary=name,
    )
    db.add(obj)
    db.commit()
    return obj


def ready(db, now):
    batch = batches.ensure_batch(db, now)
    batch.github_status = "completed"
    db.commit()
    return batch


def test_fixed_cutoff_waits_and_freezes_once(db_session, daily):
    first = article(db_session, daily, "first", analyzed=PREPARE)
    batch = ready(db_session, PREPARE)
    assert batches.prepare_batches(db_session, now=PREPARE) == []
    second = article(db_session, daily, "before-cutoff")
    queued = batches.prepare_batches(db_session, now=CUTOFF)
    assert len(queued) == 1 and queued[0].status == "pending"
    assert {i.content_id for i in queued[0].items} == {first.id, second.id}
    article(db_session, daily, "late", fetched=SEND, analyzed=SEND)
    assert batches.prepare_batches(db_session, now=SEND) == []
    assert batch.business_date == "2026-10-11"
    assert mail.utc(batch.cutoff_at) == CUTOFF


def test_old_failures_do_not_block_today_but_batch_failure_delays(db_session, daily):
    article(
        db_session,
        daily,
        "old-failed",
        fetched=PREPARE - timedelta(days=5),
        analyzed=None,
        status="failed",
    )
    current = article(db_session, daily, "required", analyzed=None, status="failed")
    batch = ready(db_session, PREPARE)
    assert batches.prepare_batches(db_session, now=SEND) == []
    assert batch.status == "delayed"
    assert "分析" in batch.error_message
    current.analysis_status = "analyzed"
    current.analyzed_at = SEND + timedelta(minutes=30)
    db_session.commit()
    result = batches.prepare_batches(db_session, now=SEND + timedelta(minutes=30))
    assert [i.content_id for i in result[0].items] == [current.id]


def test_late_arrivals_carry_to_next_day_and_empty_batch_is_final(db_session, daily):
    ready(db_session, PREPARE)
    skipped = batches.prepare_batches(db_session, now=CUTOFF)[0]
    assert skipped.status == "skipped"
    late = article(db_session, daily, "late", fetched=SEND, analyzed=SEND)
    assert batches.prepare_batches(db_session, now=SEND + timedelta(hours=1)) == []
    ready(db_session, PREPARE + timedelta(days=1))
    tomorrow = batches.prepare_batches(db_session, now=CUTOFF + timedelta(days=1))[0]
    assert [i.content_id for i in tomorrow.items] == [late.id]


def test_inflight_required_fetch_finishing_after_cutoff_is_included(db_session, daily):
    endpoint = SourceEndpoint(
        source_id=daily.id,
        name="feed",
        endpoint_type="rss",
        url="https://example.com/feed",
        enabled=True,
    )
    db_session.add(endpoint)
    db_session.commit()
    batch = ready(db_session, PREPARE)
    job = db_session.scalar(
        select(DigestBatchEndpoint).where(DigestBatchEndpoint.batch_id == batch.id)
    )
    assert batches.prepare_batches(db_session, now=SEND) == []
    late = article(
        db_session, daily, "slow-fetch", fetched=SEND, analyzed=SEND + timedelta(minutes=5)
    )
    run = FetchRun(
        endpoint_id=endpoint.id,
        status="succeeded",
        started_at=PREPARE,
        completed_at=SEND,
        content_ids=[late.id],
    )
    db_session.add(run)
    db_session.flush()
    job.run_id = run.id
    db_session.commit()
    result = batches.prepare_batches(db_session, now=SEND + timedelta(minutes=5))
    assert [i.content_id for i in result[0].items] == [late.id]


def test_batch_survives_restart_and_preferences_change(db_session, daily):
    batch = ready(db_session, PREPARE)
    mail.update_preferences(
        db_session, enabled=True, value_threshold=70, send_time="10:30", now=PREPARE
    )
    db_session.expire_all()
    same = batches.ensure_batch(db_session, PREPARE)
    assert same.id == batch.id
    assert mail.utc(same.send_at) == SEND
    assert mail.utc(same.cutoff_at) == CUTOFF


def test_sender_does_not_collect_or_create_reports(db_session, daily):
    article(db_session, daily, "ready")
    ready(db_session, PREPARE)
    queued = batches.prepare_batches(db_session, now=CUTOFF)[0]
    with patch.object(mail, "send_digest") as smtp:
        assert mail.send_queued_digest(db_session, 1, now=CUTOFF) is None
        sent = mail.send_queued_digest(db_session, 1, now=SEND)
    assert smtp.call_count == 1 and sent.id == queued.id and sent.status == "sent"
    assert (
        db_session.scalar(select(DigestBatch).where(DigestBatch.business_date == "2026-10-12"))
        is None
    )


def test_failed_source_retries_before_normal_interval(db_session, daily):
    endpoint = SourceEndpoint(
        source_id=daily.id,
        name="feed",
        endpoint_type="rss",
        url="https://example.com/feed",
        fetch_interval_minutes=1440,
    )
    db_session.add(endpoint)
    db_session.commit()
    batch = ready(db_session, PREPARE)
    run = FetchRun(
        endpoint_id=endpoint.id, status="failed", started_at=PREPARE, completed_at=PREPARE
    )
    db_session.add(run)
    db_session.commit()
    batches.refresh_batch(db_session, batch, PREPARE)
    assert batches.required_endpoint_ids(db_session, PREPARE + timedelta(minutes=1)) == []
    assert batches.required_endpoint_ids(db_session, PREPARE + timedelta(minutes=5)) == [
        endpoint.id
    ]


def test_queued_smtp_releases_database_connection(db_session, daily):
    article(db_session, daily, "ready")
    ready(db_session, PREPARE)
    batches.prepare_batches(db_session, now=CUTOFF)

    def smtp(_delivery):
        assert not db_session.in_transaction()

    with patch.object(mail, "send_digest", side_effect=smtp):
        result = mail.send_queued_digest(db_session, 1, now=SEND)
    assert result.status == "sent"


def test_status_endpoint_has_schedule_and_delayed_reason(client, db_session, daily):
    from app.security import create_access_token

    owner = db_session.scalar(select(User))
    batch = ready(db_session, PREPARE)
    article(db_session, daily, "pending", analyzed=None, status="pending")
    batches.prepare_batches(db_session, now=SEND)
    response = client.get(
        "/api/v1/email-digest/batches",
        headers={"Authorization": f"Bearer {create_access_token(str(owner.id), owner.role)}"},
    )
    assert response.status_code == 200
    latest = response.json()["items"][0]
    assert latest["id"] == batch.id and latest["status"] == "delayed"
    assert latest["analysis_pending"] == 1
    assert "分析" in latest["error_message"]


def test_deployment_has_independent_sender_and_sufficient_connection_pool():
    from pathlib import Path

    import yaml

    services = yaml.safe_load((Path(__file__).parents[3] / "compose.yaml").read_text())["services"]
    assert services["mail-sender"]["command"] == ["python", "-m", "app.mail_sender"]
    assert int(services["worker"]["environment"]["ATI_DB_POOL_SIZE"]) >= 4


def test_ordinary_pre_cutoff_fetch_must_finish_before_freezing(db_session, daily):
    endpoint = SourceEndpoint(
        source_id=daily.id,
        name="feed",
        endpoint_type="rss",
        url="https://example.com/feed",
        fetch_interval_minutes=30,
    )
    db_session.add(endpoint)
    db_session.commit()
    ready(db_session, PREPARE)
    db_session.add(
        FetchRun(
            endpoint_id=endpoint.id,
            status="succeeded",
            started_at=PREPARE,
            completed_at=PREPARE,
            content_ids=[],
        )
    )
    running = FetchRun(
        endpoint_id=endpoint.id, status="running", started_at=CUTOFF - timedelta(minutes=1)
    )
    db_session.add(running)
    db_session.commit()
    assert batches.prepare_batches(db_session, now=CUTOFF) == []
    late = article(db_session, daily, "inflight", fetched=SEND, analyzed=SEND)
    running.status = "succeeded"
    running.completed_at = SEND
    running.content_ids = [late.id]
    db_session.commit()
    result = batches.prepare_batches(db_session, now=SEND)
    assert result[0].status == "pending"
    assert [i.content_id for i in result[0].items] == [late.id]


def test_collector_status_check_does_not_overwrite_ready_batch(db_session, daily):
    batch = ready(db_session, PREPARE)
    batches.prepare_batches(db_session, now=CUTOFF)
    assert batch.status == "ready"
    assert batches.required_endpoint_ids(db_session, SEND) == []
    assert batch.status == "ready"


def test_created_batch_clock_is_not_changed_with_queued_delivery(db_session, daily):
    article(db_session, daily, "ready")
    ready(db_session, PREPARE)
    queued = batches.prepare_batches(db_session, now=CUTOFF)[0]
    mail.update_preferences(
        db_session, enabled=True, value_threshold=70, send_time="10:30", now=CUTOFF
    )
    assert mail.utc(queued.next_attempt_at) == SEND


def test_mail_date_uses_attempt_time_and_acceptance_time_is_actual(db_session, daily):
    article(db_session, daily, "ready")
    ready(db_session, PREPARE)
    queued = batches.prepare_batches(db_session, now=CUTOFF)[0]
    acceptance = SEND + timedelta(seconds=12)
    with patch.object(mail, "datetime") as clock:
        clock.now.side_effect = [SEND, acceptance]
        with patch.object(mail, "send_digest") as smtp:
            result = mail.send_queued_digest(db_session, 1)
    message = mail.build_message(smtp.call_args.args[0])
    from email.utils import parsedate_to_datetime

    assert parsedate_to_datetime(message["Date"]) == SEND
    assert mail.utc(result.sent_at) == acceptance
    assert queued.id == result.id


def test_overdue_research_retries_before_tomorrows_preparation(db_session, daily):
    from app import github_service, worker
    from app.models import GitHubExplorationState

    batch = batches.ensure_batch(db_session, PREPARE)
    batch.github_status = "failed"
    db_session.add(
        GitHubExplorationState(
            id=1, status="failed", started_at=PREPARE, finished_at=SEND, next_run_at=SEND
        )
    )
    db_session.commit()
    tomorrow_early = SEND + timedelta(hours=18)
    with (
        patch.object(worker, "engine", db_session.get_bind()),
        patch.object(worker, "datetime") as clock,
        patch.object(github_service, "run_github_exploration") as research,
    ):
        clock.now.return_value = tomorrow_early
        research.return_value = db_session.get(GitHubExplorationState, 1)
        worker.run_github_discovery()
    research.assert_called_once()
    assert str(research.call_args.kwargs["report_date"]) == batch.business_date


def test_slow_mailbox_does_not_delay_another_recipient(db_session, daily, monkeypatch):
    from threading import Event

    from app import mail_sender

    mail.save_recipient(db_session, email="second@example.com", now=PREPARE)
    article(db_session, daily, "ready")
    ready(db_session, PREPARE)
    assert len(batches.prepare_batches(db_session, now=CUTOFF)) == 2
    finish, second_sent = Event(), Event()

    def smtp(delivery):
        if delivery.recipient == "reader@example.com":
            assert finish.wait(timeout=5)
        else:
            second_sent.set()

    def tick(_seconds):
        assert second_sent.wait(timeout=3)
        finish.set()
        raise KeyboardInterrupt

    monkeypatch.setattr(mail_sender, "engine", db_session.get_bind())
    monkeypatch.setattr(mail_sender.time, "sleep", tick)
    monkeypatch.setattr(mail, "send_digest", smtp)
    try:
        with pytest.raises(KeyboardInterrupt):
            mail_sender.main()
    finally:
        finish.set()
    assert second_sent.is_set()


@pytest.mark.parametrize(
    "has_content,has_github", [(False, False), (True, False), (False, True), (True, True)]
)
def test_either_nonempty_category_is_queued(db_session, daily, has_content, has_github):
    from app.models import GitHubRepo, GitHubReport, GitHubReportItem

    batch = ready(db_session, PREPARE)
    if has_content:
        article(db_session, daily, "content")
    if has_github:
        repo = GitHubRepo(
            full_name="testing/tool",
            html_url="https://github.com/testing/tool",
            testing_value_score=40,
            intel_summary="研究摘要",
        )
        db_session.add(repo)
        db_session.flush()
        report = GitHubReport(
            report_type="daily",
            report_key="daily:2026-10-11",
            title="日报",
            status="ready",
            generated_at=PREPARE,
            items=[GitHubReportItem(repo_id=repo.id, rank=1, highlight="研究")],
        )
        db_session.add(report)
        db_session.flush()
        batch.github_report_id = report.id
        db_session.commit()
    result = batches.prepare_batches(db_session, now=CUTOFF)[0]
    assert result.status == ("pending" if has_content or has_github else "skipped")
    assert result.item_count == int(has_content) + int(has_github)


def test_ready_progress_does_not_change_when_live_content_is_reanalyzed(db_session, daily):
    item = article(db_session, daily, "ready")
    batch = ready(db_session, PREPARE)
    batches.prepare_batches(db_session, now=CUTOFF)
    item.analysis_status = "pending"
    db_session.commit()
    assert batches.batch_progress(db_session, batch)["analysis_pending"] == 0


def test_continuous_collection_before_preparation_belongs_to_today(db_session, daily):
    early = article(
        db_session,
        daily,
        "early",
        fetched=PREPARE - timedelta(hours=1),
        analyzed=None,
        status="pending",
    )
    batch = ready(db_session, PREPARE)
    assert batches.prepare_batches(db_session, now=SEND) == []
    assert batch.status == "delayed"
    early.analysis_status = "analyzed"
    early.analyzed_at = SEND + timedelta(minutes=1)
    db_session.commit()
    result = batches.prepare_batches(db_session, now=SEND + timedelta(minutes=1))[0]
    assert [i.content_id for i in result.items] == [early.id]


def test_sender_polls_only_recipients_with_due_deliveries(db_session, daily):
    from app import mail_sender

    article(db_session, daily, "ready")
    ready(db_session, PREPARE)
    batches.prepare_batches(db_session, now=CUTOFF)
    assert mail_sender.queued_recipient_ids(db_session, CUTOFF) == []
    assert mail_sender.queued_recipient_ids(db_session, SEND) == [1]


def test_fetch_links_results_to_durable_batch_job_before_network_io(db_session, daily):
    from sqlalchemy.orm import Session

    from app import fetcher

    endpoint = SourceEndpoint(
        source_id=daily.id, name="feed", endpoint_type="rss", url="https://example.com/feed"
    )
    db_session.add(endpoint)
    db_session.commit()
    batch = ready(db_session, PREPARE)
    job = db_session.scalar(
        select(DigestBatchEndpoint).where(DigestBatchEndpoint.batch_id == batch.id)
    )

    def download(*_args):
        with Session(db_session.get_bind()) as separate:
            saved = separate.get(DigestBatchEndpoint, job.id)
            assert saved.run_id is not None and saved.status == "running"
        return endpoint.url, [
            fetcher.FetchedItem(
                title="durable",
                url="https://example.com/durable",
                summary="测试摘要",
                published_at=PREPARE - timedelta(days=2),
            )
        ]

    with patch.object(fetcher, "download_feed", side_effect=download):
        run = fetcher.fetch_endpoint(db_session, endpoint, batch_job_id=job.id)
    saved_content = db_session.scalar(select(ContentItem))
    assert run.status == "succeeded" and run.content_ids == [saved_content.id]
    assert job.run_id == run.id


def test_batch_analysis_gets_priority_over_new_post_cutoff_content(db_session, daily, monkeypatch):
    from app import analyzer
    from app.config import settings

    old = article(db_session, daily, "batch", analyzed=None, status="pending")
    article(db_session, daily, "newer", fetched=SEND, analyzed=None, status="pending")
    ready(db_session, PREPARE)
    batches.prepare_batches(db_session, now=CUTOFF)
    monkeypatch.setattr(settings, "analysis_api_base_url", "https://model.example/v1")
    monkeypatch.setattr(settings, "analysis_model", "test-model")
    monkeypatch.setattr(settings, "analysis_batch_size", 1)
    with (
        patch.object(analyzer, "analyze_content") as analysis,
        patch.object(analyzer, "apply_analysis"),
    ):
        analyzer.analyze_pending(db_session, priority_ids=batches.analysis_priority_ids(db_session))
    assert analysis.call_args.args[0].id == old.id


def test_batch_does_not_materialize_already_delivered_history(db_session, daily):
    from app.models import EmailDigestDelivery, EmailDigestItem

    old = article(
        db_session,
        daily,
        "sent-before",
        fetched=PREPARE - timedelta(days=2),
        analyzed=PREPARE - timedelta(hours=23),
    )
    db_session.add(
        EmailDigestDelivery(
            recipient="reader@example.com",
            business_date="2026-10-10",
            status="sent",
            items=[EmailDigestItem(content_id=old.id, content_hash=old.url_hash, rank=1)],
        )
    )
    db_session.commit()
    batch = ready(db_session, PREPARE)
    batches.prepare_batches(db_session, now=CUTOFF)
    assert old.id not in batch.candidate_content_ids


def test_another_worker_cannot_move_running_research_attempt_boundary(db_session, daily):
    from app import github_service
    from app.models import GitHubExplorationState

    batch = batches.ensure_batch(db_session, PREPARE)
    batch.github_status = "running"
    batch.github_started_at = CUTOFF
    db_session.add(GitHubExplorationState(id=1, status="running", started_at=CUTOFF))
    db_session.commit()
    with patch.object(github_service, "run_github_exploration") as research:
        batches.run_batch_research(db_session, SEND)
    assert mail.utc(batch.github_started_at) == CUTOFF
    research.assert_not_called()
