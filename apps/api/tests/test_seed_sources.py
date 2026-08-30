from sqlalchemy import func, select

from app.models import Source, SourceEndpoint, User
from app.security import hash_password
from app.seed_sources import SAMPLE_SOURCES, seed_sources


def test_seed_sources_is_idempotent(db_session, monkeypatch):
    db_session.add(
        User(
            username="admin",
            password_hash=hash_password("password"),
            role="admin",
            is_active=True,
        )
    )
    db_session.commit()
    monkeypatch.setattr("app.seed_sources.engine", db_session.get_bind())

    first = seed_sources()
    second = seed_sources()

    assert first == (len(SAMPLE_SOURCES), len(SAMPLE_SOURCES))
    assert second == (0, 0)
    assert db_session.scalar(select(func.count()).select_from(Source)) == len(SAMPLE_SOURCES)
    assert db_session.scalar(select(func.count()).select_from(SourceEndpoint)) == len(
        SAMPLE_SOURCES
    )
    assert all(source.status == "active" for source in db_session.scalars(select(Source)))
