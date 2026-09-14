import hashlib
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


def url_digest(context) -> str:
    return hashlib.sha256(context.get_current_parameters()["url"].encode()).hexdigest()


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(30), default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    source_type: Mapped[str] = mapped_column(String(50), index=True)
    homepage_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    trust_level: Mapped[int] = mapped_column(Integer, default=3)
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    health_status: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    endpoints: Mapped[list["SourceEndpoint"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    content_items: Mapped[list["ContentItem"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class SourceEndpoint(Base):
    __tablename__ = "source_endpoints"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    endpoint_type: Mapped[str] = mapped_column(String(30), index=True)
    url: Mapped[str] = mapped_column(String(2048))
    fetch_interval_minutes: Mapped[int] = mapped_column(Integer, default=360)
    max_items_per_run: Mapped[int] = mapped_column(Integer, default=50)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    health_status: Mapped[str] = mapped_column(String(30), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    source: Mapped[Source] = relationship(back_populates="endpoints")
    fetch_runs: Mapped[list["FetchRun"]] = relationship(
        back_populates="endpoint", cascade="all, delete-orphan"
    )


class ContentItem(Base):
    __tablename__ = "content_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(2048))
    url_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, default=url_digest)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    analysis_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    analysis_attempts: Mapped[int] = mapped_column(Integer, default=0)
    testing_relevance_score: Mapped[int | None] = mapped_column(
        Integer, nullable=True, index=True
    )
    testing_value_score: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    analysis_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    testing_value_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    applicable_scenarios: Mapped[list[str]] = mapped_column(JSON, default=list)
    adoption_suggestions: Mapped[list[str]] = mapped_column(JSON, default=list)
    analysis_risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    analysis_tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    related_links: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    related_links_extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    analysis_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    analysis_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_analysis_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )

    source: Mapped[Source] = relationship(back_populates="content_items")

    @property
    def source_name(self) -> str:
        return self.source.name


class FetchRun(Base):
    __tablename__ = "fetch_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint_id: Mapped[int] = mapped_column(ForeignKey("source_endpoints.id"), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    items_created: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    endpoint: Mapped[SourceEndpoint] = relationship(back_populates="fetch_runs")
