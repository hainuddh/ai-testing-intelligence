from datetime import UTC, datetime

from app.models import ContentItem, Source, User
from app.security import create_access_token, hash_password


def add_user(db_session, username: str, role: str):
    user = User(
        username=username,
        password_hash=hash_password("password"),
        role=role,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user, {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


def add_collected_content(db_session, owner: User):
    first_source = Source(
        name="First source",
        source_type="rss",
        languages=["en"],
        topics=[],
        created_by=owner.id,
    )
    second_source = Source(
        name="Second source",
        source_type="rss",
        languages=["en"],
        topics=[],
        created_by=owner.id,
    )
    db_session.add_all([first_source, second_source])
    db_session.flush()
    pending = ContentItem(
        source_id=first_source.id,
        title="Agent testing candidate",
        url="https://example.com/pending",
        summary="Regression testing with agents",
        analysis_status="pending",
        fetched_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    analyzed = ContentItem(
        source_id=second_source.id,
        title="Visual testing intelligence",
        url="https://example.com/analyzed",
        summary="Visual quality evaluation",
        analysis_status="analyzed",
        testing_relevance_score=88,
        testing_value_score=82,
        fetched_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    failed = ContentItem(
        source_id=first_source.id,
        title="Security evaluation failure",
        url="https://example.com/failed",
        analysis_status="failed",
        analysis_error="provider unavailable",
        fetched_at=datetime(2026, 8, 28, tzinfo=UTC),
    )
    db_session.add_all([pending, analyzed, failed])
    db_session.commit()
    return first_source, pending, analyzed, failed


def test_collected_content_management_is_admin_only(client, db_session):
    _viewer, viewer_headers = add_user(db_session, "viewer", "viewer")
    _maintainer, maintainer_headers = add_user(db_session, "maintainer", "maintainer")

    assert client.get("/api/v1/collected-content").status_code == 401
    assert client.get("/api/v1/collected-content", headers=viewer_headers).status_code == 403
    assert (
        client.post(
            "/api/v1/collected-content/bulk-delete",
            headers=maintainer_headers,
            json={"content_ids": [1]},
        ).status_code
        == 403
    )

def test_admin_queries_collected_content(client, db_session):
    admin, headers = add_user(db_session, "admin", "admin")
    first_source, pending, _analyzed, _failed = add_collected_content(db_session, admin)

    response = client.get(
        "/api/v1/collected-content"
        f"?query=agent&status=pending&source_id={first_source.id}"
        "&start_at=2026-08-19T00:00:00Z&end_at=2026-08-21T00:00:00Z",
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    item = response.json()["items"][0]
    assert item["id"] == pending.id
    assert item["source_name"] == "First source"
    assert item["analysis_status"] == "pending"
    assert item["analysis_error"] is None


def test_maintainer_submits_manual_platform_content(client, db_session):
    maintainer, headers = add_user(db_session, "platform-maintainer", "maintainer")
    source = Source(
        name="Testing WeChat",
        source_type="wechat",
        languages=["zh-CN"],
        topics=["ai-testing"],
        created_by=maintainer.id,
    )
    db_session.add(source)
    db_session.commit()
    db_session.refresh(source)

    response = client.post(
        "/api/v1/collected-content",
        headers=headers,
        json={
            "source_id": source.id,
            "title": "智能体回归测试实践",
            "url": "https://mp.weixin.qq.com/s/example",
            "summary": "介绍智能体回归测试、质量评估与缺陷定位方法。",
            "published_at": "2026-09-01T00:00:00Z",
        },
    )

    assert response.status_code == 201
    item = response.json()
    assert item["source_name"] == "Testing WeChat"
    assert item["analysis_status"] == "pending"
    stored = db_session.get(ContentItem, item["id"])
    assert stored is not None
    assert stored.summary == stored.body


def test_manual_content_rejects_duplicates_missing_sources_and_viewers(client, db_session):
    maintainer, headers = add_user(db_session, "manual-maintainer", "maintainer")
    source = Source(
        name="Testing Weibo",
        source_type="weibo",
        languages=["zh-CN"],
        topics=[],
        created_by=maintainer.id,
    )
    db_session.add(source)
    db_session.commit()
    db_session.refresh(source)
    payload = {
        "source_id": source.id,
        "title": "模型评测动态",
        "url": "https://weibo.com/123456/example",
        "summary": "一条关于模型安全评测的公开信息。",
    }

    created = client.post("/api/v1/collected-content", headers=headers, json=payload)
    duplicate = client.post("/api/v1/collected-content", headers=headers, json=payload)
    assert created.status_code == 201
    assert duplicate.status_code == 409
    assert (
        client.post(
            "/api/v1/collected-content",
            headers=headers,
            json={**payload, "source_id": 99999, "url": "https://weibo.com/missing"},
        ).status_code
        == 404
    )
    _viewer, viewer_headers = add_user(db_session, "manual-viewer", "viewer")
    assert (
        client.post(
            "/api/v1/collected-content",
            headers=viewer_headers,
            json={**payload, "url": "https://weibo.com/forbidden"},
        ).status_code
        == 403
    )

    other_source = Source(
        name="Ordinary website",
        source_type="website",
        languages=["zh-CN"],
        topics=[],
        created_by=maintainer.id,
    )
    db_session.add(other_source)
    db_session.commit()
    unsupported = client.post(
        "/api/v1/collected-content",
        headers=headers,
        json={
            **payload,
            "source_id": other_source.id,
            "url": "https://example.com/manual",
        },
    )
    assert unsupported.status_code == 422


def test_admin_deletes_single_and_multiple_collected_items(client, db_session):
    admin, headers = add_user(db_session, "admin", "admin")
    _source, pending, analyzed, failed = add_collected_content(db_session, admin)

    single = client.delete(f"/api/v1/collected-content/{pending.id}", headers=headers)
    bulk = client.post(
        "/api/v1/collected-content/bulk-delete",
        headers=headers,
        json={"content_ids": [analyzed.id, failed.id]},
    )

    assert single.status_code == 204
    assert bulk.status_code == 200
    assert bulk.json() == {"deleted": 2}
    assert client.get("/api/v1/collected-content", headers=headers).json()["total"] == 0


def test_bulk_delete_rejects_empty_or_missing_ids_atomically(client, db_session):
    admin, headers = add_user(db_session, "admin", "admin")
    _source, pending, _analyzed, _failed = add_collected_content(db_session, admin)

    empty = client.post(
        "/api/v1/collected-content/bulk-delete",
        headers=headers,
        json={"content_ids": []},
    )
    missing = client.post(
        "/api/v1/collected-content/bulk-delete",
        headers=headers,
        json={"content_ids": [pending.id, 99999]},
    )

    assert empty.status_code == 422
    assert missing.status_code == 404
    assert db_session.get(ContentItem, pending.id) is not None
