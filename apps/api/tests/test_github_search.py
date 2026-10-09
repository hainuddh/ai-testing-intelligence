from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import select

from app.github_search import discover_new_candidates
from app.models import GitHubExplorationState, GitHubRepo, GitHubReport, GitHubReportItem

NOW = datetime(2026, 1, 10, tzinfo=UTC)


def _item(name="acme/new", github_id=100, **kwargs):
    return {
        "id": github_id,
        "full_name": name,
        "html_url": f"https://github.com/{name}",
        "stargazers_count": 100,
        "created_at": "2026-01-01T00:00:00Z",
        "pushed_at": "2026-01-09T00:00:00Z",
        **kwargs,
    }


def _repo(db, name, github_id=None, **kwargs):
    repo = GitHubRepo(
        full_name=name, html_url=f"https://github.com/{name}", github_id=github_id, **kwargs
    )
    db.add(repo)
    db.flush()
    return repo


def _historical(db, repo):
    report = GitHubReport(title="Old report", status="ready")
    db.add(report)
    db.flush()
    db.add(GitHubReportItem(report_id=report.id, repo_id=repo.id))
    db.commit()


def test_discovery_excludes_history_ignored_archived_and_forks(db_session):
    historical = _repo(db_session, "acme/historical", 1)
    _historical(db_session, historical)
    _repo(db_session, "acme/ignored", 2, status="ignored")
    db_session.commit()
    payload = {
        "items": [
            _item("acme/historical", 1),
            _item("acme/ignored", 2),
            _item("acme/archived", 3, archived=True),
            _item("acme/fork", 4, fork=True),
            _item(),
        ]
    }
    with patch("app.github_search.search_repositories", return_value=payload):
        assert discover_new_candidates(db_session, [], topics=["testing"], now=NOW) == 1
    assert {r.full_name for r in db_session.scalars(select(GitHubRepo))} == {
        "acme/historical",
        "acme/ignored",
        "acme/new",
    }


def test_discovery_counts_only_new_candidates_and_keeps_caller_transaction(db_session):
    _repo(db_session, "acme/existing", 1, status="watched", stars=1)
    db_session.commit()
    payload = {"items": [_item("acme/existing", 1), _item()]}
    with patch("app.github_search.search_repositories", return_value=payload):
        assert discover_new_candidates(db_session, [], topics=["testing", "ai"], now=NOW) == 1
    db_session.flush()
    assert db_session.scalar(select(GitHubRepo).where(GitHubRepo.github_id == 1)).stars == 100
    db_session.rollback()
    assert db_session.scalar(select(GitHubRepo).where(GitHubRepo.github_id == 100)) is None
    assert db_session.scalar(select(GitHubRepo).where(GitHubRepo.github_id == 1)).stars == 1


def test_renaming_a_reported_repository_does_not_bypass_exclusion(db_session):
    _historical(db_session, _repo(db_session, "acme/old-name", 1))
    with patch(
        "app.github_search.search_repositories", return_value={"items": [_item("acme/new-name", 1)]}
    ):
        assert discover_new_candidates(db_session, ["python"], now=NOW) == 0
    assert len(db_session.scalars(select(GitHubRepo)).all()) == 1


def test_legacy_reported_names_are_case_insensitive_and_gain_github_identity(db_session):
    historical = _repo(db_session, "Acme/Tool")
    _historical(db_session, historical)
    with patch(
        "app.github_search.search_repositories", return_value={"items": [_item("acme/tool", 55)]}
    ):
        assert discover_new_candidates(db_session, ["python"], now=NOW) == 0
    assert historical.github_id == 55


def test_discovery_searches_new_and_active_projects_with_normalized_topics(db_session):
    with patch("app.github_search.search_repositories", return_value={"items": []}) as search:
        discover_new_candidates(db_session, ["go"], topics=[" testing ", "testing", " "], now=NOW)
    queries = [call.args[0] for call in search.call_args_list]
    assert len(queries) == 4
    assert all("language:" not in query for query in queries)
    assert sum("topic:testing" in query for query in queries) == 2
    assert sum("testing in:name,description,topics" in query for query in queries) == 2
    assert sum("created:>=2025-10-12" in query for query in queries) == 2
    assert sum("pushed:>=2025-12-11" in query for query in queries) == 2
    assert all("archived:false" in query and "fork:false" in query for query in queries)
    assert {call.kwargs["sort"] for call in search.call_args_list} == {"stars", "updated"}


def test_blank_topics_fall_back_to_language_discovery(db_session):
    with patch("app.github_search.search_repositories", return_value={"items": []}) as search:
        discover_new_candidates(db_session, ["python"], topics=[" "], now=NOW)
    assert len(search.call_args_list) == 2
    assert all("language:python" in call.args[0] for call in search.call_args_list)


def test_search_budget_and_persistent_pagination_continue_beyond_first_pages(db_session):
    payload = {"total_count": 2000, "items": [_item(f"acme/{i}", i) for i in range(30)]}
    with patch("app.github_search.search_repositories", return_value=payload) as search:
        discover_new_candidates(db_session, ["python"], now=NOW)
        assert search.call_count == 8
        assert max(call.kwargs["page"] for call in search.call_args_list) == 4
    db_session.commit()
    state = db_session.get(GitHubExplorationState, 1)
    cursor_keys = set(state.search_cursors["pages"])
    with patch("app.github_search.search_repositories", return_value=payload) as search:
        discover_new_candidates(db_session, ["python"], now=NOW + timedelta(days=1))
        assert search.call_count == 8
        assert min(call.kwargs["page"] for call in search.call_args_list) == 5
    assert set(state.search_cursors["pages"]) == cursor_keys


def test_query_rotation_gives_later_topics_a_turn(db_session):
    with patch("app.github_search.search_repositories", return_value={"items": []}) as search:
        discover_new_candidates(db_session, [], topics=["ai", "testing", "rag"], now=NOW)
        assert search.call_count == 8
    db_session.commit()
    with patch("app.github_search.search_repositories", return_value={"items": []}) as search:
        discover_new_candidates(db_session, [], topics=["ai", "testing", "rag"], now=NOW)
        assert "rag" in search.call_args_list[0].args[0]


def test_http_failure_propagates_and_preserves_response_headers(db_session):
    request = httpx.Request("GET", "https://api.github.com/search/repositories")
    response = httpx.Response(429, headers={"Retry-After": "120"}, request=request)
    error = httpx.HTTPStatusError("rate limited", request=request, response=response)
    with patch("app.github_search.search_repositories", side_effect=error) as search:
        with pytest.raises(httpx.HTTPStatusError) as raised:
            discover_new_candidates(db_session, ["python"], now=NOW)
        assert raised.value is error
        search.assert_called_once()


def test_incomplete_search_results_are_reported_as_a_failure(db_session):
    with patch(
        "app.github_search.search_repositories",
        return_value={"incomplete_results": True, "items": []},
    ):
        with pytest.raises(RuntimeError, match="incomplete"):
            discover_new_candidates(db_session, ["python"], now=NOW)
