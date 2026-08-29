from app.models import User
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


def test_create_and_list_source(client, db_session):
    headers = auth_headers(db_session)

    created = client.post(
        "/api/v1/sources",
        headers=headers,
        json={
            "name": "Example Quality Engineering",
            "source_type": "company_blog",
            "homepage_url": "https://example.com/engineering",
            "description": "AI testing engineering practices",
            "languages": ["zh-CN", "en"],
            "trust_level": 4,
            "topics": ["ai-testing", "quality-engineering"],
        },
    )

    assert created.status_code == 201
    source = created.json()
    assert source["name"] == "Example Quality Engineering"
    assert source["status"] == "draft"
    assert source["health_status"] == "unknown"
    assert source["topics"] == ["ai-testing", "quality-engineering"]

    listed = client.get("/api/v1/sources", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == source["id"]


def test_source_rejects_invalid_trust_level(client, db_session):
    response = client.post(
        "/api/v1/sources",
        headers=auth_headers(db_session),
        json={
            "name": "Invalid source",
            "source_type": "blog",
            "languages": ["zh-CN"],
            "trust_level": 6,
            "topics": [],
        },
    )

    assert response.status_code == 422


def test_add_endpoint_to_source(client, db_session):
    headers = auth_headers(db_session)
    source = client.post(
        "/api/v1/sources",
        headers=headers,
        json={
            "name": "QE Feed",
            "source_type": "personal_blog",
            "languages": ["en"],
            "trust_level": 3,
            "topics": ["agent-testing"],
        },
    ).json()

    response = client.post(
        f"/api/v1/sources/{source['id']}/endpoints",
        headers=headers,
        json={
            "name": "Official RSS",
            "endpoint_type": "rss",
            "url": "https://example.com/feed.xml",
            "fetch_interval_minutes": 360,
            "max_items_per_run": 50,
        },
    )

    assert response.status_code == 201
    endpoint = response.json()
    assert endpoint["source_id"] == source["id"]
    assert endpoint["enabled"] is True


def test_viewer_cannot_create_source(client, db_session):
    response = client.post(
        "/api/v1/sources",
        headers=auth_headers(db_session, role="viewer"),
        json={
            "name": "Forbidden source",
            "source_type": "blog",
            "languages": ["en"],
            "trust_level": 3,
            "topics": [],
        },
    )

    assert response.status_code == 403
