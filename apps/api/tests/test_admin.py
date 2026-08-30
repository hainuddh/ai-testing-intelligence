from app.bootstrap import reset_password
from app.models import ContentItem, Source, SourceEndpoint, User
from app.security import create_access_token, hash_password, verify_password


def add_user(db_session, username, role="viewer", active=True):
    user = User(
        username=username,
        password_hash=hash_password("password"),
        role=role,
        is_active=active,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    headers = {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}
    return user, headers


def test_users_api_is_admin_only(client, db_session):
    _, viewer_headers = add_user(db_session, "viewer")

    assert client.get("/api/v1/users").status_code == 401
    assert client.get("/api/v1/users", headers=viewer_headers).status_code == 403
    assert client.get("/api/v1/database/status", headers=viewer_headers).status_code == 403


def test_admin_manages_users_with_hashed_passwords(client, db_session):
    _, headers = add_user(db_session, "admin", role="admin")

    created = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": "maintainer", "password": "secret-pass", "role": "maintainer"},
    )
    duplicate = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": "maintainer", "password": "secret-pass", "role": "viewer"},
    )
    invalid_role = client.post(
        "/api/v1/users",
        headers=headers,
        json={"username": "invalid", "password": "secret-pass", "role": "owner"},
    )

    assert created.status_code == 201
    assert created.json()["role"] == "maintainer"
    assert "password" not in created.json()
    user = db_session.get(User, created.json()["id"])
    assert user is not None
    assert user.password_hash != "secret-pass"
    assert verify_password("secret-pass", user.password_hash)
    assert duplicate.status_code == 409
    assert invalid_role.status_code == 422

    updated = client.patch(
        f"/api/v1/users/{user.id}",
        headers=headers,
        json={"role": "viewer", "password": "new-password"},
    )
    listed = client.get("/api/v1/users?limit=1", headers=headers)

    assert updated.status_code == 200
    assert updated.json()["role"] == "viewer"
    db_session.refresh(user)
    assert verify_password("new-password", user.password_hash)
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert len(listed.json()["items"]) == 1

    deleted = client.delete(f"/api/v1/users/{user.id}", headers=headers)

    assert deleted.status_code == 204


def test_admin_cannot_delete_or_deactivate_self_or_remove_last_admin(client, db_session):
    admin, headers = add_user(db_session, "admin", role="admin")

    deactivate = client.patch(
        f"/api/v1/users/{admin.id}", headers=headers, json={"is_active": False}
    )
    demote = client.patch(
        f"/api/v1/users/{admin.id}", headers=headers, json={"role": "maintainer"}
    )
    delete = client.delete(f"/api/v1/users/{admin.id}", headers=headers)

    assert deactivate.status_code == 400
    assert demote.status_code == 409
    assert delete.status_code == 400


def test_user_update_rejects_null_required_field(client, db_session):
    _, headers = add_user(db_session, "admin", role="admin")
    user, _ = add_user(db_session, "viewer")

    response = client.patch(
        f"/api/v1/users/{user.id}", headers=headers, json={"is_active": None}
    )

    assert response.status_code == 422


def test_database_status_returns_only_safe_counts(client, db_session):
    admin, headers = add_user(db_session, "admin", role="admin")
    source = Source(
        name="Counted source",
        source_type="blog",
        languages=["en"],
        topics=[],
        created_by=admin.id,
    )
    db_session.add(source)
    db_session.flush()
    db_session.add_all(
        [
            SourceEndpoint(
                source_id=source.id,
                name="Feed",
                endpoint_type="rss",
                url="https://example.com/feed",
            ),
            ContentItem(
                source_id=source.id,
                title="Article",
                url="https://example.com/article",
            ),
        ]
    )
    db_session.commit()

    response = client.get("/api/v1/database/status", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "dialect": "sqlite",
        "row_counts": {
            "users": 1,
            "sources": 1,
            "source_endpoints": 1,
            "content_items": 1,
            "fetch_runs": 0,
        },
    }


def test_reset_password_reactivates_user(db_session, monkeypatch):
    user, _ = add_user(db_session, "locked-admin", role="admin", active=False)
    old_hash = user.password_hash
    monkeypatch.setattr("app.bootstrap.engine", db_session.get_bind())

    reset_password("locked-admin", "new-secure-password")

    db_session.expire_all()
    updated = db_session.get(User, user.id)
    assert updated is not None
    assert updated.is_active is True
    assert updated.password_hash != old_hash
    assert verify_password("new-secure-password", updated.password_hash)
