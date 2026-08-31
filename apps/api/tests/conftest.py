from collections.abc import AsyncIterator, Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import Session

from app.database import Base, get_async_db, get_db
from app.main import app


@pytest.fixture
def db_session(tmp_path) -> Generator[Session, None, None]:
    db_path = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture
def client(db_session: Session, tmp_path) -> Generator[TestClient, None, None]:
    db_path = tmp_path / "test.db"
    async_engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    async def override_get_async_db() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(async_engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_async_db] = override_get_async_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    await_engine_close(async_engine)


def await_engine_close(async_engine) -> None:
    import asyncio

    try:
        asyncio.run(async_engine.dispose())
    except RuntimeError:
        pass
