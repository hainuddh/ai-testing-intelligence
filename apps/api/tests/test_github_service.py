from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import select

from app.github_analysis import RepoIntel
from app.github_service import (
    discover_candidates,
    discovery_due,
    generate_daily_report,
    get_topics,
    set_repo_status,
    set_topics,
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


def _repo_with_intel(**kwargs):
    defaults = dict(
        status="watched",
        momentum_score=88.0,
        stars=300,
        intel_summary="一个深度项目摘要",
        testing_value_analysis="对回归测试价值明确",
        applicable_scenarios=["回归测试", "CI 集成"],
        adoption_suggestions=["先小范围试点"],
        testing_value_score=85,
        intel_status="analyzed",
    )
    defaults.update(kwargs)
    return GitHubRepo(**defaults)


def test_generate_daily_report_renders_deep_intel(db_session):
    now = datetime(2026, 1, 10, tzinfo=UTC)
    db_session.add(
        _repo_with_intel(
            full_name="acme/deep",
            html_url="https://github.com/acme/deep",
            summary="轻量摘要",
        )
    )
    db_session.commit()

    report = generate_daily_report(db_session, now=now)

    assert report.body_markdown is not None
    assert "仓库地址：https://github.com/acme/deep" in report.body_markdown
    assert "**项目摘要**" in report.body_markdown
    assert "一个深度项目摘要" in report.body_markdown
    assert "**测试价值分析**" in report.body_markdown
    assert "对回归测试价值明确" in report.body_markdown
    assert "**应用场景推荐**" in report.body_markdown
    assert "回归测试" in report.body_markdown
    assert "**落地建议**" in report.body_markdown
    assert "先小范围试点" in report.body_markdown
    assert "测试价值 85/100" in report.body_markdown


def test_generate_daily_report_analyzes_when_missing(db_session):
    now = datetime(2026, 1, 10, tzinfo=UTC)
    db_session.add(
        GitHubRepo(
            full_name="acme/fresh",
            html_url="https://github.com/acme/fresh",
            status="watched",
            momentum_score=70.0,
            stars=100,
        )
    )
    db_session.commit()

    fake_intel = RepoIntel(
        summary="新项目摘要",
        testing_value_analysis="价值分析",
        applicable_scenarios=["单测增强"],
        adoption_suggestions=["评估引入"],
        testing_value_score=70,
        tags=["testing"],
    )
    with patch("app.github_service.analyze_repo_intel", return_value=fake_intel) as mock:
        report = generate_daily_report(db_session, now=now)

    mock.assert_called_once()
    assert "**项目摘要**" in report.body_markdown
    assert "新项目摘要" in report.body_markdown
    repo = db_session.query(GitHubRepo).filter_by(full_name="acme/fresh").one()
    assert repo.intel_status == "analyzed"
    assert repo.intel_summary == "新项目摘要"
    assert repo.testing_value_score == 70


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


def test_discover_candidates_queries_by_topics_when_present(db_session):
    now = datetime(2026, 1, 10, tzinfo=UTC)
    with patch("app.github_service.search_repositories", return_value=_search_payload()) as mock_search:
        count = discover_candidates(db_session, [], topics=["ai"], now=now)

    assert count == 2
    queries = [call.args[0] for call in mock_search.call_args_list]
    assert queries == [
        "topic:ai stars:>=20",
        "ai in:name,description,topics stars:>=20",
    ]


def test_get_topics_empty_by_default(db_session):
    assert get_topics(db_session) == []


def test_set_and_get_topics_normalizes(db_session):
    result = set_topics(db_session, [" ai ", "rag", "", "ai", "testing"])
    assert result == ["ai", "rag", "testing"]
    assert get_topics(db_session) == ["ai", "rag", "testing"]


def test_set_topics_replaces_existing(db_session):
    set_topics(db_session, ["ai"])
    set_topics(db_session, ["rag"])
    assert get_topics(db_session) == ["rag"]