from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.config import settings


def test_trial_samples_are_persisted_without_creating_formal_content(db_session):
    from app.models import ContentItem, Source, SourceCandidate, SourceExplorationPreference

    preference = SourceExplorationPreference(id=1, topics=["Agent testing"])
    candidate = SourceCandidate(
        name="Testing lab",
        homepage_url="https://example.com/",
        feed_url="https://example.com/feed",
        discovery_kind="catalog",
        evidence_url="https://example.com/",
        reason="Matches testing",
        samples=[{"title": "Evaluation", "url": "https://example.com/article"}],
    )
    db_session.add_all([preference, candidate])
    db_session.commit()
    with Session(db_session.get_bind()) as restarted:
        assert restarted.get(SourceCandidate, candidate.id).samples[0]["title"] == "Evaluation"
        assert restarted.get(SourceExplorationPreference, 1).max_trial_sources == 3
        assert restarted.query(Source).count() == 0
        assert restarted.query(ContentItem).count() == 0


def test_source_management_migration_preserves_sources_and_can_downgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'source-migration.db'}"
    monkeypatch.setattr(settings, "database_url", url)
    api_dir = Path(__file__).resolve().parents[1]
    config = Config(str(api_dir / "alembic.ini"))
    config.set_main_option("script_location", str(api_dir / "migrations"))
    previous = "20261011_03_digest_batches"
    command.upgrade(config, previous)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id,username,password_hash,role,is_active,created_at) "
                    "VALUES (1,'owner','unused','admin',1,CURRENT_TIMESTAMP)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO sources "
                    "(id,name,source_type,languages,trust_level,topics,status,health_status,"
                    "created_by,created_at,updated_at) VALUES "
                    "(1,'Existing source','rss','[\"en\"]',3,'[\"testing\"]','active',"
                    "'healthy',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
        command.upgrade(config, "head")
        tables = inspect(engine).get_table_names()
        assert "source_candidates" in tables
        assert "source_exploration_preferences" in tables
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT name,status,previous_status FROM sources WHERE id=1")
            ).one()
            assert tuple(row) == ("Existing source", "active", "active")
        command.downgrade(config, previous)
        assert "source_candidates" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT name FROM sources WHERE id=1")) == "Existing source"
            )
        command.upgrade(config, "head")
    finally:
        engine.dispose()
