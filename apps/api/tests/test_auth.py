from app.models import User
from app.security import hash_password


def test_login_returns_access_token(client, db_session):
    db_session.add(
        User(
            username="admin",
            password_hash=hash_password("correct-password"),
            role="admin",
            is_active=True,
        )
    )
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "correct-password"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]


def test_login_rejects_invalid_password(client, db_session):
    db_session.add(
        User(
            username="admin",
            password_hash=hash_password("correct-password"),
            role="admin",
            is_active=True,
        )
    )
    db_session.commit()

    response = client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "wrong-password"},
    )

    assert response.status_code == 401


def test_me_requires_authentication(client):
    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
