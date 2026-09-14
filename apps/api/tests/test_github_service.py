from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import select

from app.github_service import (
    discover_candidates,
    discovery_due,
    generate_daily_report,
    set_repo_status,
)
from app.models import GitHubRepo


def _search_payload():
    return {
        "items": [
            {
                "full_name": "acme/awesome",
                "description": "An awesome tool",
                "language": "Python",
                "topics": ["ai"],
                "stargazers_count": 120,
                "forks_count": 10,
                "open_issues_count": 3,
                "watchers_count": 120,
                "created_at": "2026-01-01T00:00:00Z",
                "pushed_at": "2026-01-05T00:00:00Z",
                "html_url": "https://github.com/acme/awesome",
                "homepage": None,
                "license": {"spdx_id": "MIT"},
                "archived": False,
            }
        ]
    }


def test_discover_candidates_upserts(db_session):
    now = datetime(2026, 1, 10, tzinfo=UTC)
    with patch("app.github_service.search_repositories", return_value=_search_payload()):
        count = discover_candidates(db_session, ["python"], now=now)

    assert count == 1
    repo = db_session.scalar(select(GitHubRepo).where(GitHubRepo.full_name == "acme/awesome"))
    assert repo is not None
    assert repo.primary_language == "Python"
    assert repo.stars == 120
    assert repo.status == "discovered"
    assert repo.momentum_score is not None
    assert repo.momentum_tier is not None


def test_discover_candidates_captures_languages(db_session):
    now = datetime(2026, 1, 10, tzinfo=UTC)
    payload = _search_payload()
    payload["items"][0]["full_name"] = "acme/auth-tool"
    payload["items"][0]["language"] = "Go"
    payload["items"][0]["topics"] = ["security"]

    with patch("app.github_service.search_repositories", return_value=payload):
        discover_candidates(db_session, ["go", "rust"], now=now)

    repo = db_session.scalar(select(GitHubRepo).where(GitHubRepo.full_name == "acme/auth-tool"))
    assert repo.primary_language == "Go"
    assert repo.topics == ["security"]


def test_set_repo_status(db_session):
    repo = GitHubRepo(full_name="acme/x", html_url="https://github.com/acme/x", status="discovered")
    db_session.add(repo)
    db_session.commit()

    updated = set_repo_status(db_session, repo.id, "watched")
    assert updated is not None
    assert updated.status == "watched"

    assert set_repo_status(db_session, 9999, "watched") is None


def test_generate_daily_report_only_watched(db_session):
    now = datetime(2026, 1, 10, tzinfo=UTC)
    r1 = GitHubRepo(full_name="a/one", html_url="https://github.com/a/one", status="watched", momentum_score=90.0, stars=100)
    r2 = GitHubRepo(full_name="b/two", html_url="https://github.com/b/two", status="watched", momentum_score=50.0, stars=50)
    r3 = GitHubRepo(full_name="c/three", html_url="https://github.com/c/three", status="discovered", momentum_score=99.0, stars=200)
    db_session.add_all([r1, r2, r3])
    db_session.commit()

    report = generate_daily_report(db_session, now=now)

    assert report.report_type == "daily"
    assert len(report.items) == 2
    assert report.items[0].repo_id == r1.id
    assert report.items[1].repo_id == r2.id
    assert report.body_markdown is not None
    assert "a/one" in report.body_markdown
    assert "b/two" in report.body_markdown
    assert "c/three" not in report.body_markdown


def test_discovery_due_no_repos(db_session):
    now = datetime(2026, 1, 10, tzinfo=UTC)
    assert discovery_due(db_session, now=now) is True


def test_discovery_due_recent(db_session):
    now = datetime(2026, 1, 10, tzinfo=UTC)
    db_session.add(GitHubRepo(full_name="a/b", html_url="https://github.com/a/b", last_seen_at=now))
    db_session.commit()
    assert discovery_due(db_session, now=now) is False


def test_discovery_due_stale(db_session):
    now = datetime(2026, 1, 10, tzinfo=UTC)
    stale = now - timedelta(hours=25)
    db_session.add(GitHubRepo(full_name="a/b", html_url="https://github.com/a/b", last_seen_at=stale))
    db_session.commit()
    assert discovery_due(db_session, now=now) is True