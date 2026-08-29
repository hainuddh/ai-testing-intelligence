import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import engine
from app.fetcher import fetch_endpoint
from app.models import FetchRun, SourceEndpoint


def due_endpoints(db: Session) -> list[SourceEndpoint]:
    last_run = (
        select(FetchRun.endpoint_id, func.max(FetchRun.started_at).label("last_started_at"))
        .group_by(FetchRun.endpoint_id)
        .subquery()
    )
    endpoints = db.execute(
        select(SourceEndpoint, last_run.c.last_started_at)
        .outerjoin(last_run, SourceEndpoint.id == last_run.c.endpoint_id)
        .where(SourceEndpoint.enabled)
    ).all()
    now = datetime.now(UTC)
    return [
        endpoint
        for endpoint, last_started_at in endpoints
        if last_started_at is None
        or last_started_at.replace(tzinfo=last_started_at.tzinfo or UTC)
        <= now - timedelta(minutes=endpoint.fetch_interval_minutes)
    ]


def run_once() -> None:
    if engine.dialect.name != "postgresql":
        with Session(engine) as db:
            for endpoint in due_endpoints(db):
                try:
                    fetch_endpoint(db, endpoint)
                except Exception:
                    db.rollback()
        return

    with engine.connect() as lock_connection:
        if not lock_connection.scalar(text("SELECT pg_try_advisory_lock(82429101)")):
            return
        try:
            with Session(engine) as db:
                for endpoint in due_endpoints(db):
                    try:
                        fetch_endpoint(db, endpoint)
                    except Exception:
                        db.rollback()
        finally:
            lock_connection.execute(text("SELECT pg_advisory_unlock(82429101)"))


def main() -> None:
    while True:
        run_once()
        time.sleep(settings.worker_poll_seconds)


if __name__ == "__main__":
    main()
