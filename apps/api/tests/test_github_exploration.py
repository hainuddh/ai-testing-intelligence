from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import github_service as service
from app.github_analysis import RepoIntel
from app.models import GitHubRepo, GitHubReport, GitHubReportItem

NOW = datetime(2026, 10, 9, 17, tzinfo=UTC)
INTEL = RepoIntel("项目摘要", "测试价值", ["自动回归"], ["试点"], 85, ["testing"])


def repo(db, name, **kwargs):
    obj = GitHubRepo(full_name=name, html_url=f"https://github.com/{name}", **kwargs)
    db.add(obj)
    db.commit()
    return obj


def published(db, obj):
    report = GitHubReport(title="已有研究", generated_at=NOW - timedelta(days=3))
    db.add(report)
    db.flush()
    report.items.append(GitHubReportItem(repo_id=obj.id, rank=1))
    db.commit()
    return report


def test_reports_automatically_select_new_candidates_and_exclude_all_history(db_session):
    old = repo(db_session, "test/old", momentum_score=99, intel_status="failed")
    history = published(db_session, old)
    fresh = repo(db_session, "test/fresh", momentum_score=80)
    repo(db_session, "test/ignored", status="ignored", momentum_score=98)
    repo(db_session, "test/archived", archived=True, momentum_score=97)
    with patch.object(service, "analyze_repo_intel", return_value=INTEL) as analyze:
        report = service.generate_daily_report(db_session, now=NOW)
    assert [item.repo_id for item in report.items] == [fresh.id]
    assert analyze.call_count == 1
    assert "2026-10-10" in report.title  # Asia/Shanghai business day.
    assert db_session.get(GitHubReport, history.id).items[0].repo_id == old.id


def test_same_business_day_reuses_report_and_next_day_excludes_previous_items(db_session):
    first = repo(db_session, "test/first", momentum_score=80)
    with patch.object(service, "analyze_repo_intel", return_value=INTEL):
        report = service.generate_daily_report(db_session, now=NOW)
        repo(db_session, "test/second", momentum_score=90)
        repeated = service.generate_daily_report(db_session, now=NOW + timedelta(hours=1))
        tomorrow = service.generate_daily_report(db_session, now=NOW + timedelta(days=1))
    assert report.id == repeated.id
    assert first.id not in [item.repo_id for item in tomorrow.items]
    assert len(tomorrow.items) == 1


def test_failed_analysis_is_not_published_and_can_retry(db_session):
    obj = repo(db_session, "test/retry", momentum_score=70)
    with patch.object(service, "analyze_repo_intel", side_effect=RuntimeError("private secret")):
        assert service.generate_daily_report(db_session, now=NOW) is None
    assert db_session.scalar(select(func.count(GitHubReportItem.id))) == 0
    state = service.get_exploration_state(db_session)
    assert state.status == "failed"
    assert "private secret" not in (state.error_message or "")
    with patch.object(service, "analyze_repo_intel", return_value=INTEL):
        report = service.generate_daily_report(db_session, now=NOW + timedelta(hours=2))
    assert report.items[0].repo_id == obj.id


def test_reports_filter_current_topics_and_testing_value_and_limit_five(db_session):
    service.set_topics(db_session, ["testing"])
    repo(db_session, "test/unrelated", topics=["finance"], momentum_score=100)
    low = repo(db_session, "test/low", topics=["testing"], momentum_score=99)
    for i in range(7):
        repo(db_session, f"test/relevant-{i}", topics=["testing"], momentum_score=90 - i)

    def analyze(name, *_args, **_kwargs):
        if name == low.full_name:
            return RepoIntel("低价值", "无直接价值", [], [], 20, [])
        return INTEL

    with patch.object(service, "analyze_repo_intel", side_effect=analyze) as mock:
        report = service.generate_daily_report(db_session, now=NOW)
    assert len(report.items) == 5
    assert low.id not in [item.repo_id for item in report.items]
    assert all(call.args[0] != "test/unrelated" for call in mock.call_args_list)


def test_empty_run_persists_schedule_and_does_not_make_empty_report(db_session):
    with patch.object(service, "discover_candidates", return_value=0) as discover:
        service.run_github_exploration(db_session, now=NOW)
        service.run_github_exploration(db_session, now=NOW + timedelta(minutes=1))
    assert discover.call_count == 1
    assert service.discovery_due(db_session, now=NOW + timedelta(minutes=1)) is False
    assert db_session.scalar(select(func.count(GitHubReport.id))) == 0
    assert service.get_exploration_state(db_session).status == "completed"
    assert service.get_exploration_state(db_session).report_id is None


def test_exploration_reads_topics_and_automatically_publishes(db_session):
    service.set_topics(db_session, ["testing"])
    obj = repo(db_session, "test/tool", topics=["testing"])
    with (
        patch.object(service, "discover_candidates", return_value=1) as discover,
        patch.object(service, "analyze_repo_intel", return_value=INTEL),
    ):
        state = service.run_github_exploration(db_session, now=NOW)
    assert discover.call_args.kwargs["topics"] == ["testing"]
    assert state.status == "completed"
    assert state.discovered_count == 1
    assert db_session.get(GitHubReport, state.report_id).items[0].repo_id == obj.id
    service.set_repo_status(db_session, obj.id, "watched")
    assert service.discovery_due(db_session, now=NOW + timedelta(hours=25)) is True


def test_search_failure_backs_off_and_retries_without_exposing_secrets(db_session):
    with patch.object(service, "discover_candidates", side_effect=RuntimeError("token=secret")):
        state = service.run_github_exploration(db_session, now=NOW)
    assert state.status == "failed"
    assert state.next_run_at is not None
    assert "secret" not in state.error_message
    assert not service.discovery_due(db_session, now=NOW + timedelta(minutes=1))
    assert service.discovery_due(db_session, now=NOW + timedelta(hours=2))


def test_claim_prevents_concurrent_work_and_expired_owner_cannot_publish(db_session):
    token = service.claim_exploration(db_session, now=NOW)
    assert token
    with Session(db_session.get_bind()) as other:
        assert service.claim_exploration(other, now=NOW, force=True) is None
        replacement = service.claim_exploration(other, now=NOW + timedelta(hours=2))
        assert replacement and replacement != token
    with pytest.raises(service.ExplorationLeaseLost):
        service.finish_exploration(db_session, token, now=NOW, report=None, discovered=0)


def test_worker_runs_pipeline_without_manual_request(db_session):
    from app import worker

    with (
        patch.object(worker, "engine", db_session.get_bind()),
        patch.object(service, "discover_candidates", return_value=0) as discover,
    ):
        worker.run_github_discovery()
    discover.assert_called_once()
    db_session.expire_all()
    assert service.get_exploration_state(db_session).status == "completed"


def test_failed_publication_rolls_back_report_and_history(db_session):
    from sqlalchemy import event

    obj = repo(db_session, "test/unpublished", momentum_score=70)

    def reject_report(_mapper, _connection, _target):
        raise RuntimeError("publication failed")

    event.listen(GitHubReportItem, "before_insert", reject_report)
    try:
        with patch.object(service, "analyze_repo_intel", return_value=INTEL):
            assert service.generate_daily_report(db_session, now=NOW) is None
    finally:
        event.remove(GitHubReportItem, "before_insert", reject_report)
    assert db_session.scalar(select(func.count(GitHubReport.id))) == 0
    assert db_session.scalar(select(func.count(GitHubReportItem.id))) == 0
    with patch.object(service, "analyze_repo_intel", return_value=INTEL):
        report = service.generate_daily_report(db_session, now=NOW + timedelta(hours=2))
    assert report.items[0].repo_id == obj.id


def test_worker_respects_upstream_retry_after(db_session):
    import httpx

    request = httpx.Request("GET", "https://api.github.com/search/repositories")
    response = httpx.Response(429, headers={"Retry-After": "7200"}, request=request)
    error = httpx.HTTPStatusError("rate limit", request=request, response=response)
    with patch.object(service, "discover_candidates", side_effect=error):
        state = service.run_github_exploration(db_session, now=NOW)
    assert service._utc(state.next_run_at) == NOW + timedelta(hours=2)


def test_expired_search_cannot_overwrite_replacement_cursor(db_session):
    from app.models import GitHubExplorationState

    def replaced_search(db, *_args, **_kwargs):
        with Session(db.get_bind()) as other:
            assert service.claim_exploration(other, now=NOW + timedelta(hours=2))
            replacement = other.get(GitHubExplorationState, 1)
            replacement.search_cursors = {"replacement": True}
            other.commit()
        stale = db.get(GitHubExplorationState, 1)
        stale.search_cursors = {"stale": True}
        return 0

    with patch.object(service, "discover_candidates", side_effect=replaced_search):
        state = service.run_github_exploration(db_session, now=NOW)
    assert state.status == "running"
    assert state.search_cursors == {"replacement": True}
