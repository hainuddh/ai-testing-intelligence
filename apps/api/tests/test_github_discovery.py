from datetime import UTC, datetime, timedelta

from app.github_discovery import (
    build_discovery_queries,
    estimate_star_deltas,
    repo_from_search_item,
    score_candidate,
)


def test_build_discovery_queries():
    assert build_discovery_queries(["python", "go"]) == [
        "language:python stars:>=20",
        "language:go stars:>=20",
    ]


def test_build_discovery_queries_custom_min():
    assert build_discovery_queries(["rust"], stars_min=100) == ["language:rust stars:>=100"]


def test_repo_from_search_item_maps_fields():
    item = {
        "full_name": "owner/repo",
        "description": "desc",
        "language": "Python",
        "topics": ["ai", "agents"],
        "stargazers_count": 1500,
        "forks_count": 40,
        "open_issues_count": 3,
        "watchers_count": 1500,
        "created_at": "2025-01-01T00:00:00Z",
        "pushed_at": "2026-01-01T00:00:00Z",
        "html_url": "https://github.com/owner/repo",
        "homepage": "https://repo.example",
        "license": {"spdx_id": "MIT", "name": "MIT License"},
        "archived": True,
    }
    repo = repo_from_search_item(item)
    assert repo.full_name == "owner/repo"
    assert repo.language == "Python"
    assert repo.topics == ["ai", "agents"]
    assert repo.stars == 1500
    assert repo.license_name == "MIT"
    assert repo.archived is True
    assert repo.created_at == datetime(2025, 1, 1, tzinfo=UTC)


def test_repo_from_search_item_handles_missing_optionals():
    item = {"full_name": "a/b"}
    repo = repo_from_search_item(item)
    assert repo.description == ""
    assert repo.language is None
    assert repo.topics == []
    assert repo.stars == 0
    assert repo.license_name is None
    assert repo.created_at is None


def test_estimate_star_deltas_fresh_repo():
    now = datetime(2026, 1, 8, tzinfo=UTC)
    created = datetime(2026, 1, 1, tzinfo=UTC)  # 7 天前
    d24h, d7d = estimate_star_deltas(140, created, now)
    assert d24h == 20  # 140 / 7
    assert d7d == 140


def test_estimate_star_deltas_floors_age():
    now = datetime(2026, 1, 8, tzinfo=UTC)
    created = now - timedelta(hours=1)  # 极新，age 被钳到 0.5 天
    d24h, _ = estimate_star_deltas(50, created, now)
    assert d24h == 100  # 50 / 0.5


def test_score_candidate_delegates_to_momentum():
    now = datetime(2026, 1, 8, tzinfo=UTC)
    m = score_candidate(1000, star_delta_24h=30, star_delta_7d=150, pushed_at=None, now=now)
    assert m.tier == "new_notable"