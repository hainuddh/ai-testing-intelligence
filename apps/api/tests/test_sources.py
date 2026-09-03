from unittest.mock import patch

from app.fetcher import FetchedItem
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

    listed = client.get(f"/api/v1/sources/{source['id']}/endpoints", headers=headers)
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == endpoint["id"]

    deleted = client.delete(
        f"/api/v1/sources/{source['id']}/endpoints/{endpoint['id']}", headers=headers
    )
    assert deleted.status_code == 204


def test_maintainer_discovers_previews_and_installs_source(client, db_session):
    headers = auth_headers(db_session)
    discovered = FetchedItem(
        title="Agent testing update",
        url="https://example.com/article",
        summary="Testing summary",
    )
    with patch(
        "app.routers.sources.download_feed",
        return_value=("https://example.com/feed.xml", [discovered]),
    ) as download:
        preview = client.post(
            "/api/v1/sources/discover",
            headers=headers,
            json={"homepage_url": "https://www.example.com/news/"},
        )
        installed = client.post(
            "/api/v1/sources/discover/install",
            headers=headers,
            json={
                "homepage_url": "https://www.example.com/news/",
                "feed_url": "https://example.com/feed.xml",
                "name": "Example Engineering",
                "languages": ["en"],
                "trust_level": 4,
                "topics": ["agent-testing"],
            },
        )

    assert preview.status_code == 200
    assert preview.json()["suggested_name"] == "example.com"
    assert preview.json()["samples"][0]["title"] == "Agent testing update"
    assert installed.status_code == 201
    assert installed.json()["source"]["status"] == "active"
    assert installed.json()["endpoint"]["url"] == "https://example.com/feed.xml"
    assert download.call_count == 2


def test_source_discovery_rejects_invalid_feed_and_viewers(client, db_session):
    maintainer_headers = auth_headers(db_session)
    with patch("app.routers.sources.download_feed", side_effect=ValueError("invalid")):
        invalid = client.post(
            "/api/v1/sources/discover",
            headers=maintainer_headers,
            json={"homepage_url": "https://example.com/"},
        )
    viewer_headers = auth_headers(db_session, role="viewer")
    forbidden = client.post(
        "/api/v1/sources/discover",
        headers=viewer_headers,
        json={"homepage_url": "https://example.com/"},
    )

    assert invalid.status_code == 422
    assert forbidden.status_code == 403


def test_platform_sources_accept_rss_but_reject_web_endpoints(client, db_session):
    headers = auth_headers(db_session)
    for source_type in ("wechat", "weibo"):
        source = client.post(
            "/api/v1/sources",
            headers=headers,
            json={
                "name": f"Platform {source_type}",
                "source_type": source_type,
                "languages": ["zh-CN"],
            },
        )
        assert source.status_code == 201
        source_id = source.json()["id"]
        rss = client.post(
            f"/api/v1/sources/{source_id}/endpoints",
            headers=headers,
            json={
                "name": "Authorized feed",
                "endpoint_type": "rss",
                "url": f"https://example.com/{source_type}.xml",
            },
        )
        web = client.post(
            f"/api/v1/sources/{source_id}/endpoints",
            headers=headers,
            json={
                "name": "Platform page",
                "endpoint_type": "web",
                "url": f"https://example.com/{source_type}",
            },
        )
        assert rss.status_code == 201
        assert web.status_code == 422
        assert "only support RSS/Atom" in web.json()["detail"]


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


def test_only_admin_can_delete_source(client, db_session):
    maintainer_headers = auth_headers(db_session)
    source = client.post(
        "/api/v1/sources",
        headers=maintainer_headers,
        json={
            "name": "Protected collected content",
            "source_type": "blog",
            "languages": ["en"],
            "topics": [],
        },
    ).json()

    response = client.delete(f"/api/v1/sources/{source['id']}", headers=maintainer_headers)

    assert response.status_code == 403


def test_source_update_rejects_null_required_field(client, db_session):
    headers = auth_headers(db_session)
    source = client.post(
        "/api/v1/sources",
        headers=headers,
        json={
            "name": "Required fields",
            "source_type": "blog",
            "languages": ["en"],
            "topics": [],
        },
    ).json()

    response = client.patch(
        f"/api/v1/sources/{source['id']}", headers=headers, json={"name": None}
    )

    assert response.status_code == 422


def test_maintainer_can_update_and_admin_can_delete_source(client, db_session):
    headers = auth_headers(db_session)
    first = client.post(
        "/api/v1/sources",
        headers=headers,
        json={
            "name": "First source",
            "source_type": "blog",
            "languages": ["en"],
        },
    ).json()
    client.post(
        "/api/v1/sources",
        headers=headers,
        json={
            "name": "Existing source",
            "source_type": "blog",
            "languages": ["en"],
        },
    )

    updated = client.patch(
        f"/api/v1/sources/{first['id']}",
        headers=headers,
        json={"description": "Updated", "trust_level": 5},
    )
    duplicate = client.patch(
        f"/api/v1/sources/{first['id']}",
        headers=headers,
        json={"name": "Existing source"},
    )
    admin_headers = auth_headers(db_session, role="admin")
    deleted = client.delete(f"/api/v1/sources/{first['id']}", headers=admin_headers)
    missing = client.delete(f"/api/v1/sources/{first['id']}", headers=admin_headers)

    assert updated.status_code == 200
    assert updated.json()["description"] == "Updated"
    assert updated.json()["trust_level"] == 5
    assert duplicate.status_code == 409
    assert deleted.status_code == 204
    assert missing.status_code == 404


def test_viewer_cannot_update_or_delete_source(client, db_session):
    maintainer_headers = auth_headers(db_session)
    source = client.post(
        "/api/v1/sources",
        headers=maintainer_headers,
        json={
            "name": "Protected source",
            "source_type": "blog",
            "languages": ["en"],
        },
    ).json()
    viewer_headers = auth_headers(db_session, role="viewer")

    assert (
        client.patch(
            f"/api/v1/sources/{source['id']}",
            headers=viewer_headers,
            json={"name": "No access"},
        ).status_code
        == 403
    )
    assert (
        client.delete(
            f"/api/v1/sources/{source['id']}", headers=viewer_headers
        ).status_code
        == 403
    )
