from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select
from test_sources import auth_headers

from app.models import ContentItem, FetchRun, Source, SourceEndpoint


def source_fixture(db, name, status="active", *, enabled=True, topics=None, language="en"):
    source = Source(
        name=name,
        source_type="blog",
        homepage_url=f"https://{name.lower()}.example.com",
        languages=[language],
        topics=topics or ["testing"],
        status=status,
        created_by=1,
    )
    db.add(source)
    db.flush()
    endpoint = SourceEndpoint(
        source_id=source.id,
        name="Feed",
        endpoint_type="rss",
        url=f"https://{name.lower()}.example.com/feed",
        enabled=enabled,
    )
    db.add(endpoint)
    db.commit()
    return source, endpoint


def test_source_filters_paginate_but_summary_covers_all_sources(client, db_session):
    headers = auth_headers(db_session)
    source_fixture(db_session, "Alpha", topics=["agents"], language="zh-CN")
    source_fixture(db_session, "Beta", status="paused")
    source_fixture(db_session, "Gamma", status="archived")
    source_fixture(db_session, "Delta", status="draft", enabled=False)
    result = client.get(
        "/api/v1/sources",
        headers=headers,
        params={"q": "ALPHA.EXAMPLE", "topic": "agents", "language": "zh-CN", "limit": 1},
    )
    assert result.status_code == 200
    data = result.json()
    assert data["total"] == 1
    assert data["items"][0]["name"] == "Alpha"
    assert data["summary"] == {
        "total": 4,
        "active": 1,
        "paused": 1,
        "archived": 1,
        "draft": 1,
        "needs_attention": 1,
    }
    assert (
        client.get(
            "/api/v1/sources",
            headers=headers,
            params={"q": "%"},
        ).json()["total"]
        == 0
    )
    assert (
        client.get(
            "/api/v1/sources",
            headers=headers,
            params={"topic": "agent"},
        ).json()["total"]
        == 0
    )
    assert (
        client.get(
            "/api/v1/sources",
            headers=headers,
            params={"status": "paused"},
        ).json()["items"][0]["name"]
        == "Beta"
    )
    assert (
        client.get(
            "/api/v1/sources",
            headers=headers,
            params={"offset": 4, "limit": 1},
        ).json()["items"]
        == []
    )


def test_metrics_separate_collection_health_from_content_analysis(client, db_session):
    headers = auth_headers(db_session)
    source, endpoint = source_fixture(db_session, "Evidence")
    now = datetime.now(UTC)
    db_session.add_all(
        [
            FetchRun(
                endpoint_id=endpoint.id,
                status="succeeded",
                started_at=now - timedelta(days=3),
                completed_at=now - timedelta(days=3),
                items_created=0,
            ),
            FetchRun(
                endpoint_id=endpoint.id,
                status="failed",
                started_at=now - timedelta(hours=1),
                completed_at=now - timedelta(hours=1),
                error="Connection timed out",
            ),
            FetchRun(endpoint_id=endpoint.id, status="running", started_at=now),
        ]
    )
    for index, (analysis, disposition, age, score) in enumerate(
        [
            ("analyzed", "radar", 1, 90),
            ("analyzed", "watch", 2, 50),
            ("analyzed", None, 2, 80),
            ("pending", None, 3, None),
            ("failed", None, 4, None),
            ("analyzed", "radar", 31, 95),
        ]
    ):
        db_session.add(
            ContentItem(
                source_id=source.id,
                title=f"Sample {index}",
                url=f"https://sample.example/{index}",
                fetched_at=now - timedelta(days=age),
                analysis_status=analysis,
                analysis_disposition=disposition,
                testing_relevance_score=score,
                testing_value_score=score,
            )
        )
    db_session.commit()
    listed = client.get("/api/v1/sources?needs_attention=true", headers=headers).json()
    assert listed["total"] == 1
    metrics = listed["items"][0]["metrics"]
    assert metrics["window_days"] == 30
    assert metrics["collected_count"] == 5
    assert metrics["analyzed_count"] == 3
    assert metrics["radar_count"] == 2
    assert metrics["pending_count"] == 1
    assert metrics["failed_count"] == 1
    assert metrics["health_status"] == "unhealthy"
    assert metrics["last_success_at"] is not None
    assert metrics["last_error"] == "Connection timed out"
    assert len(metrics["sample_items"]) == 3
    assert all(item["title"] != "Sample 5" for item in metrics["sample_items"])
    assert listed["summary"]["needs_attention"] == 1


def test_disabled_endpoint_failure_and_low_frequency_do_not_mark_source_unhealthy(
    client,
    db_session,
):
    headers = auth_headers(db_session)
    source, endpoint = source_fixture(db_session, "LowFrequency")
    old = datetime.now(UTC) - timedelta(days=40)
    disabled = SourceEndpoint(
        source_id=source.id,
        name="Disabled",
        endpoint_type="rss",
        url="https://disabled.example/feed",
        enabled=False,
        health_status="unhealthy",
    )
    db_session.add(disabled)
    db_session.add(
        FetchRun(
            endpoint_id=endpoint.id,
            status="succeeded",
            started_at=old,
            completed_at=old,
        )
    )
    db_session.commit()
    metrics = client.get("/api/v1/sources", headers=headers).json()["items"][0]["metrics"]
    assert metrics["health_status"] == "healthy"
    assert metrics["needs_attention"] is False
    assert metrics["collected_count"] == 0
    assert metrics["endpoint_count"] == 2
    assert metrics["enabled_endpoint_count"] == 1


def test_lifecycle_preserves_history_and_endpoint_choices(client, db_session):
    headers = auth_headers(db_session)
    source, endpoint = source_fixture(db_session, "Lifecycle")
    disabled = SourceEndpoint(
        source_id=source.id,
        name="Disabled",
        endpoint_type="rss",
        url="https://disabled.example/feed",
        enabled=False,
    )
    content = ContentItem(source_id=source.id, title="Kept", url="https://sample.example/kept")
    db_session.add_all([disabled, content])
    db_session.commit()
    for action, expected in [
        ("pause", "paused"),
        ("pause", "paused"),
        ("archive", "archived"),
        ("archive", "archived"),
        ("restore", "paused"),
        ("resume", "active"),
    ]:
        response = client.post(
            f"/api/v1/sources/{source.id}/lifecycle",
            headers=headers,
            json={"action": action},
        )
        assert response.status_code == 200
        assert response.json()["status"] == expected
    db_session.expire_all()
    assert db_session.get(SourceEndpoint, endpoint.id).enabled is True
    assert db_session.get(SourceEndpoint, disabled.id).enabled is False
    assert db_session.get(ContentItem, content.id).title == "Kept"
    assert (
        client.post(
            f"/api/v1/sources/{source.id}/lifecycle",
            headers=headers,
            json={"action": "restore"},
        ).status_code
        == 409
    )


def test_lifecycle_requires_maintainer_and_draft_resume_does_not_claim_listening(
    client,
    db_session,
):
    headers = auth_headers(db_session)
    source, _ = source_fixture(db_session, "Draft", status="draft", enabled=False)
    viewer = auth_headers(db_session, "viewer")
    assert (
        client.post(
            f"/api/v1/sources/{source.id}/lifecycle",
            headers=viewer,
            json={"action": "pause"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/api/v1/sources/{source.id}/lifecycle",
            headers=headers,
            json={"action": "pause"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/sources/{source.id}/lifecycle",
            headers=headers,
            json={"action": "resume"},
        ).json()["status"]
        == "draft"
    )


def test_endpoint_edit_is_scoped_validated_and_can_enable_draft(client, db_session):
    headers = auth_headers(db_session)
    source, endpoint = source_fixture(db_session, "Editable", status="draft", enabled=False)
    source.source_type = "wechat"
    other, _ = source_fixture(db_session, "Other")
    db_session.commit()
    target = f"/api/v1/sources/{source.id}/endpoints/{endpoint.id}"
    assert client.patch(target, headers=headers, json={"endpoint_type": "web"}).status_code == 422
    assert client.patch(target, headers=headers, json={"enabled": None}).status_code == 422
    assert (
        client.patch(target, headers=headers, json={"fetch_interval_minutes": 1}).status_code == 422
    )
    assert (
        client.patch(
            f"/api/v1/sources/{other.id}/endpoints/{endpoint.id}",
            headers=headers,
            json={"name": "Wrong"},
        ).status_code
        == 404
    )
    response = client.patch(
        target,
        headers=headers,
        json={
            "name": "Updated",
            "url": "https://editable.example/new-feed",
            "fetch_interval_minutes": 720,
            "max_items_per_run": 20,
            "enabled": True,
        },
    )
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["fetch_interval_minutes"] == 720
    db_session.expire_all()
    assert db_session.get(Source, source.id).status == "active"


def test_new_endpoint_activates_draft_source(client, db_session):
    headers = auth_headers(db_session)
    source, _ = source_fixture(db_session, "NewEndpoint", status="draft", enabled=False)
    response = client.post(
        f"/api/v1/sources/{source.id}/endpoints",
        headers=headers,
        json={
            "name": "New",
            "endpoint_type": "rss",
            "url": "https://new.example/feed",
        },
    )
    assert response.status_code == 201
    db_session.expire_all()
    assert db_session.get(Source, source.id).status == "active"


def test_manual_source_without_feed_is_not_reported_as_collection_failure(client, db_session):
    headers = auth_headers(db_session)
    db_session.add(
        Source(
            name="Manual publication",
            source_type="wechat",
            languages=["zh"],
            topics=["testing"],
            created_by=1,
            status="active",
        )
    )
    db_session.commit()
    result = client.get("/api/v1/sources", headers=headers).json()
    assert result["items"][0]["metrics"]["needs_attention"] is False
    assert result["summary"]["needs_attention"] == 0


@pytest.mark.parametrize(
    "url",
    [
        "https://duplicate.example.com:443/feed#section",
        "https://DUPLICATE.example.com/feed",
    ],
)
def test_install_rejects_normalized_feed_duplicate_without_losing_other_columns(
    client,
    db_session,
    url,
):
    headers = auth_headers(db_session)
    source, _ = source_fixture(db_session, "Duplicate")
    payload = {
        "name": "Second installation",
        "homepage_url": "https://duplicate.example.com",
        "feed_url": url,
        "languages": ["en"],
    }
    with patch("app.routers.sources.download_feed", return_value=(url, [])):
        response = client.post("/api/v1/sources/discover/install", headers=headers, json=payload)
    assert response.status_code == 409
    assert len(db_session.scalars(select(Source)).all()) == 1
    payload["feed_url"] = "https://duplicate.example.com/other-feed"
    with patch("app.routers.sources.download_feed", return_value=(payload["feed_url"], [])):
        assert (
            client.post(
                "/api/v1/sources/discover/install",
                headers=headers,
                json=payload,
            ).status_code
            == 201
        )


def test_install_rejects_feed_redirect_to_already_installed_endpoint(client, db_session):
    headers = auth_headers(db_session)
    source_fixture(db_session, "Redirect")
    with patch(
        "app.routers.sources.download_feed",
        return_value=("https://redirect.example.com/feed", []),
    ):
        response = client.post(
            "/api/v1/sources/discover/install",
            headers=headers,
            json={
                "name": "Redirected",
                "homepage_url": "https://new.example.com",
                "feed_url": "https://new.example.com/feed",
                "languages": ["en"],
            },
        )
    assert response.status_code == 409
