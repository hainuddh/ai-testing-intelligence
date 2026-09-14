from unittest.mock import patch

from app.models import GitHubRepo, User
from app.security import create_access_token, hash_password


def auth_headers(db_session, role="maintainer"):
    user = User(
        username=f"user-{role}",
        password_hash=hash_password("password"),
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id, user.role)
    return {"Authorization": f"Bearer {token}"}


def _search_payload():
    return {
        "items": [
            {
                "full_name": "acme/newthing",
                "description": "a new thing",
                "language": "Python",
                "topics": ["ai", "llm"],
                "stargazers_count": 250,
                "forks_count": 20,
                "open_issues_count": 5,
                "watchers_count": 250,
                "created_at": "2026-01-01T00:00:00Z",
                "pushed_at": "2026-01-08T00:00:00Z",
                "html_url": "https://github.com/acme/newthing",
                "homepage": None,
                "license": {"spdx_id": "MIT"},
                "archived": False,
            }
        ]
    }


def test_discover_creates_candidates(client, db_session):
    headers = auth_headers(db_session)
    with patch("app.github_service.search_repositories", return_value=_search_payload()):
        resp = client.post(
            "/api/v1/github/discover", json={"languages": ["python"]}, headers=headers
        )
    assert resp.status_code == 200
    assert resp.json()["discovered"] == 1

    repo = db_session.query(GitHubRepo).filter_by(full_name="acme/newthing").first()
    assert repo is not None
    assert repo.stars == 250


def test_list_repos_returns_candidates(client, db_session):
    headers = auth_headers(db_session)
    db_session.add(
        GitHubRepo(
            full_name="acme/existing",
            html_url="https://github.com/acme/existing",
            status="watched",
            stars=100,
        )
    )
    db_session.commit()

    resp = client.get("/api/v1/github/repos", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(r["full_name"] == "acme/existing" for r in data["items"])


def test_update_repo_status(client, db_session):
    headers = auth_headers(db_session)
    repo = GitHubRepo(
        full_name="acme/x",
        html_url="https://github.com/acme/x",
        status="discovered",
        stars=10,
    )
    db_session.add(repo)
    db_session.commit()
    db_session.refresh(repo)

    resp = client.patch(
        f"/api/v1/github/repos/{repo.id}/status",
        json={"status": "watched"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "watched"


def test_update_repo_status_404(client, db_session):
    headers = auth_headers(db_session)
    resp = client.patch(
        "/api/v1/github/repos/99999/status",
        json={"status": "watched"},
        headers=headers,
    )
    assert resp.status_code == 404


def test_generate_and_list_report(client, db_session):
    headers = auth_headers(db_session)
    repo = GitHubRepo(
        full_name="acme/watched",
        html_url="https://github.com/acme/watched",
        status="watched",
        stars=500,
        summary="轻量摘要",
        momentum_score=80.0,
        momentum_tier="rising",
    )
    db_session.add(repo)
    db_session.commit()
    db_session.refresh(repo)

    resp = client.post("/api/v1/github/reports/generate", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["report_type"] == "daily"

    list_resp = client.get("/api/v1/github/reports", headers=headers)
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert data["total"] >= 1
    assert data["items"][0]["items"][0]["repo_id"] == repo.id


def test_get_and_put_github_preferences(client, db_session):
    headers = auth_headers(db_session)

    empty = client.get("/api/v1/github/preferences", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["topics"] == []

    put = client.put(
        "/api/v1/github/preferences", json={"topics": ["ai", "rag", "ai"]}, headers=headers
    )
    assert put.status_code == 200
    assert put.json()["topics"] == ["ai", "rag"]

    fetched = client.get("/api/v1/github/preferences", headers=headers)
    assert fetched.json()["topics"] == ["ai", "rag"]


def test_list_repos_filters_by_topic(client, db_session):
    headers = auth_headers(db_session)
    db_session.add_all(
        [
            GitHubRepo(full_name="a/ai", html_url="https://github.com/a/ai", stars=10, topics=["ai", "agents"]),
            GitHubRepo(full_name="b/web", html_url="https://github.com/b/web", stars=20, topics=["web"]),
        ]
    )
    db_session.commit()

    resp = client.get("/api/v1/github/repos?topic=ai", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["full_name"] == "a/ai"