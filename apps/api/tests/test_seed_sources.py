from sqlalchemy import func, select

from app.models import ContentItem, Source, SourceEndpoint, User
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

    assert first == (0, len(SAMPLE_SOURCES), len(SAMPLE_SOURCES))
    assert second == (0, 0, 0)
    assert db_session.scalar(select(func.count()).select_from(Source)) == len(SAMPLE_SOURCES)
    assert db_session.scalar(select(func.count()).select_from(SourceEndpoint)) == len(
        SAMPLE_SOURCES
    )
    assert all(source.status == "active" for source in db_session.scalars(select(Source)))


def test_reset_recreates_only_sample_sources(db_session, monkeypatch):
    owner = User(
        username="admin",
        password_hash=hash_password("password"),
        role="admin",
        is_active=True,
    )
    db_session.add(owner)
    db_session.flush()
    custom = Source(
        name="Custom testing source",
        source_type="website",
        languages=["zh-CN"],
        topics=["testing"],
        created_by=owner.id,
    )
    db_session.add(custom)
    db_session.commit()
    monkeypatch.setattr("app.seed_sources.engine", db_session.get_bind())
    seed_sources()
    sample = db_session.scalar(select(Source).where(Source.name == SAMPLE_SOURCES[0].name))
    assert sample is not None
    db_session.add(
        ContentItem(
            source_id=sample.id,
            title="Old sample content",
            url="https://example.com/old-sample-content",
        )
    )
    db_session.commit()

    removed, created, endpoints = seed_sources(reset=True)

    db_session.expire_all()
    assert (removed, created, endpoints) == (
        len(SAMPLE_SOURCES),
        len(SAMPLE_SOURCES),
        len(SAMPLE_SOURCES),
    )
    assert db_session.scalar(select(Source).where(Source.id == custom.id)) is not None
    old_content = db_session.scalar(
        select(ContentItem).where(ContentItem.title == "Old sample content")
    )
    assert old_content is None
