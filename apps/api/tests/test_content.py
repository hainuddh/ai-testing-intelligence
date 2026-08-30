from datetime import UTC, datetime

from app.models import ContentItem, Source, User
from app.security import create_access_token, hash_password


def user_headers(db_session, username="viewer", role="viewer"):
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


def test_content_requires_authentication(client):
    assert client.get("/api/v1/content").status_code == 401


def test_list_filter_and_get_content(client, db_session):
    user, headers = user_headers(db_session)
    first_source = Source(
        name="First",
        source_type="blog",
        languages=["en"],
        topics=[],
        created_by=user.id,
    )
    second_source = Source(
        name="Second",
        source_type="blog",
        languages=["en"],
        topics=[],
        created_by=user.id,
    )
    db_session.add_all([first_source, second_source])
    db_session.flush()
    matching = ContentItem(
        source_id=first_source.id,
        title="Reliable agent testing",
        url="https://example.com/agent-testing",
        summary="A practical guide",
        published_at=datetime(2026, 8, 20, tzinfo=UTC),
    )
    db_session.add_all(
        [
            matching,
            ContentItem(
                source_id=second_source.id,
                title="Other article",
                url="https://example.com/other",
                body="Unrelated content",
            ),
        ]
    )
    db_session.commit()
    db_session.refresh(matching)

    response = client.get(
        f"/api/v1/content?source_id={first_source.id}&query=agent&limit=1",
        headers=headers,
    )
    detail = client.get(f"/api/v1/content/{matching.id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["title"] == "Reliable agent testing"
    assert detail.status_code == 200
    assert detail.json()["source_id"] == first_source.id
    assert client.get("/api/v1/content/9999", headers=headers).status_code == 404

    ranged = client.get(
        "/api/v1/content?start_at=2026-08-19T00:00:00Z&end_at=2026-08-21T00:00:00Z",
        headers=headers,
    )
    invalid_range = client.get(
        "/api/v1/content?start_at=2026-08-22T00:00:00Z&end_at=2026-08-21T00:00:00Z",
        headers=headers,
    )

    assert ranged.status_code == 200
    assert ranged.json()["total"] == 1
    assert ranged.json()["items"][0]["id"] == matching.id
    assert invalid_range.status_code == 422
