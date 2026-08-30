import argparse
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import engine
from app.models import Source, SourceEndpoint, User


@dataclass(frozen=True)
class SampleSource:
    name: str
    homepage_url: str
    description: str
    topics: tuple[str, ...]
    feed_url: str
    fetch_interval_minutes: int = 360
    max_items_per_run: int = 20


SAMPLE_SOURCES = (
    SampleSource(
        name="OpenAI News",
        homepage_url="https://openai.com/news/",
        description="OpenAI 官方产品、研究、工程、安全和公司动态。",
        topics=("foundation-models", "agents", "ai-safety", "openai"),
        feed_url="https://openai.com/news/rss.xml",
        fetch_interval_minutes=180,
    ),
    SampleSource(
        name="Google AI Blog",
        homepage_url="https://blog.google/innovation-and-ai/technology/ai/",
        description="Google 的 AI 产品、研究、开发工具和行业应用动态。",
        topics=("gemini", "google-ai", "agents", "ai-products"),
        feed_url="https://blog.google/technology/ai/rss/",
    ),
    SampleSource(
        name="Google DeepMind",
        homepage_url="https://deepmind.google/discover/blog/",
        description="Google DeepMind 的前沿模型、科学研究和 AI 安全进展。",
        topics=("research", "deepmind", "ai-safety", "science"),
        feed_url="https://deepmind.google/blog/rss.xml",
    ),
    SampleSource(
        name="MIT Technology Review AI",
        homepage_url="https://www.technologyreview.com/topic/artificial-intelligence/",
        description="MIT Technology Review 的人工智能新闻、分析和深度报道。",
        topics=("ai-news", "policy", "industry", "research"),
        feed_url="https://www.technologyreview.com/topic/artificial-intelligence/feed/",
    ),
    SampleSource(
        name="Machine Learning Mastery",
        homepage_url="https://machinelearningmastery.com/",
        description="面向开发者的机器学习、LLM、RAG 和 AI Agent 实践教程。",
        topics=("machine-learning", "llm", "rag", "agents"),
        feed_url="https://machinelearningmastery.com/feed/",
        fetch_interval_minutes=720,
    ),
)


def seed_samples(session: Session, user: User) -> tuple[int, int]:
    sources_created = 0
    endpoints_created = 0
    for sample in SAMPLE_SOURCES:
        source = session.scalar(select(Source).where(Source.name == sample.name))
        if source is None:
            source = Source(
                name=sample.name,
                source_type="website",
                homepage_url=sample.homepage_url,
                description=sample.description,
                languages=["en"],
                trust_level=5,
                topics=list(sample.topics),
                status="active",
                created_by=user.id,
            )
            session.add(source)
            session.flush()
            sources_created += 1
        endpoint = session.scalar(
            select(SourceEndpoint).where(SourceEndpoint.url == sample.feed_url)
        )
        if endpoint is None:
            session.add(
                SourceEndpoint(
                    source_id=source.id,
                    name="Official RSS / Atom",
                    endpoint_type="rss",
                    url=sample.feed_url,
                    fetch_interval_minutes=sample.fetch_interval_minutes,
                    max_items_per_run=sample.max_items_per_run,
                )
            )
            endpoints_created += 1
    return sources_created, endpoints_created


def seed_sources(username: str = "admin", *, reset: bool = False) -> tuple[int, int, int]:
    with Session(engine) as session:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            raise SystemExit(f"User {username!r} does not exist")
        removed = 0
        if reset:
            if session.get_bind().dialect.name == "postgresql":
                session.execute(text("SELECT pg_advisory_xact_lock(82429101)"))
            sample_names = [sample.name for sample in SAMPLE_SOURCES]
            sources = list(session.scalars(select(Source).where(Source.name.in_(sample_names))))
            for source in sources:
                session.delete(source)
            session.flush()
            removed = len(sources)
        sources_created, endpoints_created = seed_samples(session, user)
        session.commit()
        return removed, sources_created, endpoints_created


def main() -> None:
    parser = argparse.ArgumentParser(description="Add sample AI news sources")
    parser.add_argument("--username", default="admin", help="Owner of newly created sources")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete and recreate only the built-in sample sources and their data",
    )
    args = parser.parse_args()
    removed, sources, endpoints = seed_sources(args.username, reset=args.reset)
    print(f"Removed {removed}, created {sources} sources and {endpoints} endpoints")


if __name__ == "__main__":
    main()
