from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import settings

MERGE_REVISION = "20260924_01_merge_github_tracking"
PARENT_REVISIONS = ["20260923_01_analysis_disposition", "baa79d86eefc"]


def migration_config(output_buffer=None):
    api_dir = Path(__file__).resolve().parents[1]
    config = Config(str(api_dir / "alembic.ini"), output_buffer=output_buffer)
    config.set_main_option("script_location", str(api_dir / "migrations"))
    return config


@pytest.mark.parametrize("starting_revision", PARENT_REVISIONS)
def test_postgres_expands_version_capacity_before_recording_merge(
    monkeypatch, starting_revision
):
    monkeypatch.setattr(settings, "database_url", "postgresql+psycopg://localhost/unused")
    output = StringIO()

    command.upgrade(migration_config(output), f"{starting_revision}:head", sql=True)

    sql = output.getvalue()
    expand = "ALTER TABLE alembic_version ALTER COLUMN version_num TYPE VARCHAR(64)"
    record_merge = f"SET version_num='{MERGE_REVISION}'"
    assert expand in sql
    assert sql.index(expand) < sql.index(record_merge)


@pytest.mark.parametrize("starting_revision", PARENT_REVISIONS)
def test_sqlite_upgrade_and_retry_preserve_existing_data(
    monkeypatch, tmp_path, starting_revision
):
    db_url = f"sqlite:///{tmp_path / 'migration.db'}"
    monkeypatch.setattr(settings, "database_url", db_url)
    config = migration_config()
    command.upgrade(config, starting_revision)
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO users (id, username, password_hash, role, is_active, created_at) "
                "VALUES (1, 'existing-user', 'test-only-hash', 'viewer', 1, '2026-09-01')"
            ))

        command.upgrade(config, "head")
        command.upgrade(config, "head")

        with engine.connect() as connection:
            revisions = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalars().all()
            assert revisions == [MERGE_REVISION]
            assert connection.execute(text("SELECT username FROM users")).scalars().all() == [
                "existing-user"
            ]
    finally:
        engine.dispose()
