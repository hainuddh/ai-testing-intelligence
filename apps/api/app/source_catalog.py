"""Maintained starting points. Every subscription is verified before trial admission."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogSource:
    name: str
    homepage_url: str
    topics: tuple[str, ...]
    language: str = "en"
    feed_url: str | None = None


CATALOG = (
    CatalogSource("Google Testing Blog", "https://testing.googleblog.com/", ("testing", "quality")),
    CatalogSource("Martin Fowler", "https://martinfowler.com/", ("testing", "engineering")),
    CatalogSource(
        "Playwright Releases",
        "https://github.com/microsoft/playwright/releases",
        ("testing", "automation", "browser"),
        feed_url="https://github.com/microsoft/playwright/releases.atom",
    ),
    CatalogSource("Cypress Blog", "https://www.cypress.io/blog/", ("testing", "automation")),
    CatalogSource(
        "pytest Releases",
        "https://github.com/pytest-dev/pytest/releases",
        ("testing", "python"),
        feed_url="https://github.com/pytest-dev/pytest/releases.atom",
    ),
    CatalogSource(
        "Hugging Face Blog",
        "https://huggingface.co/blog",
        ("llm", "agents", "evaluation"),
        feed_url="https://huggingface.co/blog/feed.xml",
    ),
    CatalogSource(
        "OpenAI News",
        "https://openai.com/news/",
        ("llm", "agents", "ai-safety"),
        feed_url="https://openai.com/news/rss.xml",
    ),
    CatalogSource(
        "Google AI Blog",
        "https://blog.google/technology/ai/",
        ("llm", "agents", "research"),
        feed_url="https://blog.google/technology/ai/rss/",
    ),
    CatalogSource(
        "Google DeepMind",
        "https://deepmind.google/discover/blog/",
        ("llm", "research"),
        feed_url="https://deepmind.google/blog/rss.xml",
    ),
    CatalogSource(
        "Machine Learning Mastery",
        "https://machinelearningmastery.com/",
        ("llm", "agents", "rag"),
        feed_url="https://machinelearningmastery.com/feed/",
    ),
)
