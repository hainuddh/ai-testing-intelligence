from collections.abc import AsyncIterator, Generator

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session

from app.config import settings


class Base(DeclarativeBase):
    pass


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}

_pool_kwargs = (
    {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_pre_ping": True,
        "pool_recycle": 1800,
    }
    if not settings.database_url.startswith("sqlite")
    else {}
)

# Synchronous engine for the worker, seeding scripts and admin CLI.
engine = create_engine(settings.database_url, connect_args=connect_args, **_pool_kwargs)

# Asynchronous engine for the API server (lower thread/memory footprint).
_async_url = settings.database_url
if _async_url.startswith("sqlite://"):
    _async_url = _async_url.replace("sqlite://", "sqlite+aiosqlite://", 1)
async_engine = create_async_engine(_async_url, connect_args=connect_args, **_pool_kwargs)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


async def get_async_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
