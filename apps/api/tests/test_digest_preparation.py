from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app import email_digest as service
from app import github_service, worker
from app.config import settings
from app.models import (
    ContentItem,
    EmailDigestDelivery,
    FetchRun,
    GitHubExplorationState,
    GitHubRepo,
    GitHubReport,
    GitHubReportItem,
    Source,
    SourceEndpoint,
    User,
)

NOW = datetime(2026, 10, 11, 1, tzinfo=UTC)  # 09:00 Beijing.
DAY_START = NOW - timedelta(hours=9)


@pytest.fixture
def prepared(db_session, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "radar@example.com")
    service.save_recipient(db_session, email="reader@example.com", now=DAY_START)
    service.update_preferences(
        db_session, enabled=True, value_threshold=70, send_time="09:00", now=DAY_START
    )
    owner = User(username="owner", password_hash="unused", role="admin")
    db_session.add(owner)
    db_session.flush()
    source = Source(name="Testing", source_type="blog", created_by=owner.id)
    state = GitHubExplorationState(
        id=1,
        status="completed",
        started_at=NOW - timedelta(minutes=30),
        finished_at=NOW,
        next_run_at=NOW + timedelta(days=1),
    )
    db_session.add_all([source, state])
    db_session.commit()
    return source, state


def content(db, source, **fields):
    values = dict(
        source_id=source.id,
        title="Griffin",
        url="https://example.com/griffin",
        published_at=NOW - timedelta(days=1),
        fetched_at=NOW - timedelta(minutes=20),
        analyzed_at=NOW - timedelta(minutes=5),
        analysis_status="analyzed",
        analysis_disposition="radar",
        testing_relevance_score=72,
        testing_value_score=80,
        analysis_summary="当日分析的旧文章",
        adoption_suggestions=["试点"],
    )
    values.update(fields)
    obj = ContentItem(**values)
    db.add(obj)
    db.commit()
    return obj


def endpoint(db, source, **fields):
    obj = SourceEndpoint(
        source_id=source.id,
        name="feed",
        endpoint_type="rss",
        url="https://example.com/feed",
        fetch_interval_minutes=1440,
        **fields,
    )
    db.add(obj)
    db.commit()
    return obj


def github_report(db, state, *, value=85, now=NOW, name="testing/tool"):
    repo = db.scalar(select(GitHubRepo).where(GitHubRepo.full_name == name))
    if repo is None:
        repo = GitHubRepo(
            full_name=name,
            html_url=f"https://github.com/{name}",
            stars=500,
            intel_status="analyzed",
            intel_summary="研究摘要",
            testing_value_score=value,
            testing_value_analysis="用于回归测试",
            applicable_scenarios=["自动回归"],
            adoption_suggestions=["试点"],
        )
        db.add(repo)
        db.flush()
    report = GitHubReport(
        report_type="daily",
        title="GitHub项目日报",
        status="ready",
        generated_at=now,
        items=[GitHubReportItem(repo_id=repo.id, rank=1, highlight=repo.intel_summary)],
    )
    db.add(report)
    db.flush()
    state.report_id = report.id
    db.commit()
    return report, repo


@pytest.mark.parametrize("status", ["pending", "failed"])
def test_waits_for_all_content_analysis_without_recording_empty_day(db_session, prepared, status):
    source, _ = prepared
    obj = content(
        db_session,
        source,
        analysis_status=status,
        analyzed_at=None,
        next_analysis_at=NOW + timedelta(minutes=10),
    )
    with patch.object(service, "send_digest") as send:
        assert service.run_daily_digest(db_session, now=NOW) == []
        assert db_session.scalar(select(EmailDigestDelivery)) is None
        obj.analysis_status = "analyzed"
        obj.analyzed_at = NOW + timedelta(minutes=10)
        db_session.commit()
        result = service.run_daily_digest(db_session, now=NOW + timedelta(minutes=11))
    assert result[0].status == "sent"
    send.assert_called_once()


@pytest.mark.parametrize("status", [None, "running", "failed", "yesterday"])
def test_waits_for_today_collection_success(db_session, prepared, status):
    source, _ = prepared
    feed = endpoint(db_session, source)
    content(db_session, source)
    if status is not None:
        instant = NOW - timedelta(days=1) if status == "yesterday" else NOW
        db_session.add(
            FetchRun(
                endpoint_id=feed.id,
                status="succeeded" if status == "yesterday" else status,
                started_at=instant,
                completed_at=None if status == "running" else instant,
            )
        )
        db_session.commit()
    with patch.object(service, "send_digest") as send:
        assert service.run_daily_digest(db_session, now=NOW) == []
    assert db_session.scalar(select(EmailDigestDelivery)) is None
    send.assert_not_called()


@pytest.mark.parametrize("status", ["never_run", "running", "failed", "yesterday"])
def test_waits_for_today_github_research_including_successful_empty_run(
    db_session, prepared, status
):
    source, state = prepared
    content(db_session, source)
    state.status = "completed" if status == "yesterday" else status
    if status == "yesterday":
        state.started_at -= timedelta(days=1)
        state.finished_at -= timedelta(days=1)
    db_session.commit()
    with patch.object(service, "send_digest") as send:
        assert service.run_daily_digest(db_session, now=NOW) == []
    assert db_session.scalar(select(EmailDigestDelivery)) is None
    send.assert_not_called()


def test_collects_publication_from_yesterday_after_today_analysis(db_session, prepared):
    source, _ = prepared
    obj = content(db_session, source)
    feed = endpoint(db_session, source)
    db_session.add(
        FetchRun(
            endpoint_id=feed.id,
            status="succeeded",
            started_at=NOW - timedelta(minutes=20),
            completed_at=NOW - timedelta(minutes=19),
        )
    )
    db_session.commit()
    with patch.object(service, "send_digest"):
        result = service.run_daily_digest(db_session, now=NOW)
    assert result[0].status == "sent"
    assert [receipt.content_id for receipt in result[0].items] == [obj.id]


def test_genuinely_empty_day_is_skipped_but_later_results_can_send(db_session, prepared):
    source, _ = prepared
    with patch.object(service, "send_digest") as send:
        empty = service.run_daily_digest(db_session, now=NOW)[0]
        assert empty.status == "skipped"
        assert service.run_daily_digest(db_session, now=NOW + timedelta(minutes=1)) == []
        content(db_session, source, analyzed_at=NOW + timedelta(minutes=5))
        ready = service.run_daily_digest(db_session, now=NOW + timedelta(minutes=6))[0]
        assert ready.id == empty.id and ready.status == "sent"
        assert service.run_daily_digest(db_session, now=NOW + timedelta(minutes=7)) == []
    send.assert_called_once()


def test_combines_content_and_github_and_freezes_full_research(db_session, prepared):
    source, state = prepared
    article = content(db_session, source)
    report, repo = github_report(db_session, state)
    with patch.object(service, "send_digest") as send:
        delivery = service.run_daily_digest(db_session, now=NOW)[0]
    assert delivery.item_count == 2
    assert [receipt.content_id for receipt in delivery.items] == [article.id]
    assert [receipt.repo_key for receipt in delivery.github_items] == [repo.full_name]
    assert delivery.github_items[0].report_id == report.id
    assert "Griffin" in delivery.body_text and "testing/tool" in delivery.body_text
    assert "研究摘要" in delivery.body_html and "用于回归测试" in delivery.body_markdown
    assert (
        "自动回归" in delivery.body_markdown
        and "https://github.com/testing/tool" in delivery.body_html
    )
    send.assert_called_once()


@pytest.mark.parametrize("value", [60, 70, 71])
def test_nonempty_github_daily_report_alone_sends_without_email_score_filter(
    db_session, prepared, value
):
    _, state = prepared
    github_report(db_session, state, value=value)
    with patch.object(service, "send_digest") as send:
        delivery = service.run_daily_digest(db_session, now=NOW)[0]
    assert delivery.status == "sent" and delivery.item_count == 1
    assert delivery.items == []
    assert len(delivery.github_items) == 1
    send.assert_called_once()


@pytest.mark.parametrize(
    ("has_content", "has_research", "expected_status"),
    [(False, False, "skipped"), (True, False, "sent"), (False, True, "sent"), (True, True, "sent")],
)
def test_only_both_empty_skips_mail(
    db_session, prepared, has_content, has_research, expected_status
):
    source, state = prepared
    if has_content:
        content(db_session, source)
    if has_research:
        github_report(db_session, state, value=60)
    with patch.object(service, "send_digest") as send:
        delivery = service.run_daily_digest(db_session, now=NOW)[0]
    assert delivery.status == expected_status
    assert delivery.item_count == int(has_content) + int(has_research)
    assert send.call_count == int(has_content or has_research)


@pytest.mark.parametrize(
    "changed_fields", [{"intel_status": "failed"}, {"status": "ignored"}, {"archived": True}]
)
def test_published_nonempty_report_is_not_refiltered_by_current_repo_state(
    db_session, prepared, changed_fields
):
    _, state = prepared
    _, repo = github_report(db_session, state, value=60)
    for field, value in changed_fields.items():
        setattr(repo, field, value)
    db_session.commit()
    with patch.object(service, "send_digest") as send:
        delivery = service.run_daily_digest(db_session, now=NOW)[0]
    assert delivery.status == "sent" and delivery.item_count == 1
    assert "testing/tool" in delivery.body_markdown
    send.assert_called_once()


def test_github_retry_keeps_snapshot_and_deduplicates_per_mailbox(db_session, prepared):
    _, state = prepared
    _, repo = github_report(db_session, state)
    with patch.object(service, "send_digest", side_effect=RuntimeError("smtp error")):
        delivery = service.run_daily_digest(db_session, now=NOW)[0]
    frozen = delivery.body_markdown
    repo.intel_summary = "后来改变的摘要"
    db_session.commit()
    with patch.object(service, "send_digest"):
        result = service.run_daily_digest(db_session, now=NOW + timedelta(minutes=6))[0]
        assert result.body_markdown == frozen and result.status == "sent"
        tomorrow = NOW + timedelta(days=1)
        state.started_at = tomorrow - timedelta(minutes=30)
        state.finished_at = tomorrow
        github_report(db_session, state, now=tomorrow)
        assert service.run_daily_digest(db_session, now=tomorrow)[0].status == "skipped"
        service.save_recipient(db_session, email="second@example.com", now=NOW)
        results = service.run_daily_digest(db_session, now=tomorrow + timedelta(minutes=1))
    assert len(results) == 1 and results[0].recipient == "second@example.com"
    assert results[0].status == "sent"


def test_long_source_interval_does_not_skip_daily_preparation(db_session, prepared):
    source, _ = prepared
    feed = endpoint(db_session, source)
    db_session.add(
        FetchRun(
            endpoint_id=feed.id,
            status="succeeded",
            started_at=NOW - timedelta(hours=12),
            completed_at=NOW - timedelta(hours=12),
        )
    )
    db_session.commit()
    with patch.object(worker, "datetime") as clock:
        clock.now.return_value = NOW
        assert [obj.id for obj in worker.due_endpoints(db_session)] == [feed.id]


def test_worker_runs_github_before_mail(db_session):
    calls = []
    with (
        patch.object(worker, "engine", db_session.get_bind()),
        patch.object(worker, "run_analysis", side_effect=lambda: calls.append("analysis")),
        patch.object(worker, "run_github_discovery", side_effect=lambda: calls.append("github")),
        patch.object(worker, "run_email_digest", side_effect=lambda: calls.append("mail")),
    ):
        worker.run_once()
    assert calls == ["analysis", "github", "mail"]


def test_postgres_worker_holds_collection_lock_until_mail_is_prepared():
    calls = []
    engine = MagicMock()
    engine.dialect.name = "postgresql"
    connection = engine.connect.return_value.__enter__.return_value
    connection.scalar.side_effect = lambda _sql: calls.append("lock") or True
    connection.execute.side_effect = lambda _sql: calls.append("unlock")
    with (
        patch.object(worker, "engine", engine),
        patch.object(worker, "Session"),
        patch.object(worker, "due_endpoints", return_value=[]),
        patch.object(worker, "run_analysis", side_effect=lambda: calls.append("analysis")),
        patch.object(worker, "run_github_discovery", side_effect=lambda: calls.append("github")),
        patch.object(worker, "run_email_digest", side_effect=lambda: calls.append("mail")),
    ):
        worker.run_once()
    assert calls == ["lock", "analysis", "github", "mail", "unlock"]


def test_github_24_hour_schedule_cannot_leave_today_unprepared(db_session, prepared):
    _, state = prepared
    state.started_at -= timedelta(days=1)
    state.finished_at -= timedelta(days=1)
    state.next_run_at = NOW + timedelta(hours=2)
    db_session.commit()
    with (
        patch.object(worker, "engine", db_session.get_bind()),
        patch.object(worker, "datetime") as clock,
        patch.object(github_service, "datetime") as research_clock,
        patch.object(github_service, "discover_candidates", return_value=0) as discover,
    ):
        clock.now.return_value = NOW
        research_clock.now.return_value = NOW
        research_clock.combine.side_effect = datetime.combine
        research_clock.min = datetime.min
        worker.run_github_discovery()
    discover.assert_called_once()
    db_session.expire_all()
    assert db_session.get(GitHubExplorationState, 1).status == "completed"


def test_github_failures_keep_retry_backoff_even_when_mail_due(db_session, prepared):
    _, state = prepared
    state.status = "failed"
    state.next_run_at = datetime.now(UTC) + timedelta(hours=2)
    db_session.commit()
    with (
        patch.object(worker, "engine", db_session.get_bind()),
        patch.object(github_service, "discover_candidates") as discover,
    ):
        worker.run_github_discovery()
    discover.assert_not_called()


def test_worker_records_actual_github_finish_time(db_session, prepared):
    _, state = prepared
    state.started_at -= timedelta(days=1)
    state.finished_at -= timedelta(days=1)
    db_session.commit()
    finished = NOW + timedelta(minutes=3)
    with (
        patch.object(worker, "engine", db_session.get_bind()),
        patch.object(worker, "datetime") as clock,
        patch.object(github_service, "datetime") as research_clock,
        patch.object(github_service, "discover_candidates", return_value=0),
    ):
        clock.now.return_value = NOW
        research_clock.now.side_effect = [NOW, finished]
        research_clock.combine.side_effect = datetime.combine
        research_clock.min = datetime.min
        worker.run_github_discovery()
    db_session.expire_all()
    assert service.utc(db_session.get(GitHubExplorationState, 1).finished_at) == finished
