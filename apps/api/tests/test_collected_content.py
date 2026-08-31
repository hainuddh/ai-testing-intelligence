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
