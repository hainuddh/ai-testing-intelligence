from unittest.mock import patch

import pytest

from app.fetcher import (
    download,
    download_feed,
    fetch_endpoint,
    parse_feed,
    parse_web,
    validate_public_url,
)
from app.models import ContentItem, FetchRun, Source, SourceEndpoint, User
from app.security import hash_password


def add_endpoint(db_session):
    user = User(
        username="fetch-owner",
        password_hash=hash_password("password"),
        role="maintainer",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    source = Source(
        name="Fetch source",
        source_type="rss",
        languages=["en"],
        topics=[],
        created_by=user.id,
    )
    db_session.add(source)
    db_session.flush()
    endpoint = SourceEndpoint(
        source_id=source.id,
        name="Feed",
        endpoint_type="rss",
        url="https://example.com/feed.xml",
    )
    db_session.add(endpoint)
    db_session.commit()
    db_session.refresh(endpoint)
    return endpoint


def test_parse_rss_and_web_content():
    feed = b"""<?xml version="1.0"?>
    <rss xmlns:media="http://search.yahoo.com/mrss/"><channel><item>
    <title>New release</title><link>/article</link>
    <media:description>Hero image</media:description>
    <description>&lt;img src="hero.jpg"&gt;Release
    &lt;strong&gt;summary&lt;/strong&gt;</description>
    </item></channel></rss>"""

    items = parse_feed(feed, "https://example.com/feed.xml", 10)
    page = parse_web(
        b'<html><head><title>Page title</title><meta name="description" '
        b'content="Metadata summary"><script>noise</script></head><p>Useful body</p></html>',
        "https://example.com",
    )

    assert items[0].url == "https://example.com/article"
    assert items[0].summary == "Release summary"
    assert page[0].title == "Page title"
    assert page[0].summary == "Metadata summary"
    assert "Useful body" in (page[0].body or "")
    assert "noise" not in (page[0].body or "")


def test_parse_rss_publication_date():
    feed = b"""<rss><channel><item><title>Dated article</title>
    <link>https://example.com/dated</link>
    <pubDate>Wed, 26 Aug 2026 19:00:00 +0000</pubDate></item></channel></rss>"""

    item = parse_feed(feed, "https://example.com/feed.xml", 10)[0]

    assert item.published_at is not None
    assert item.published_at.isoformat() == "2026-08-26T19:00:00+00:00"


def test_download_feed_discovers_and_persists_advertised_feed(db_session):
    endpoint = add_endpoint(db_session)
    endpoint.url = "https://example.com/news/"
    page = b"""<html><head><link rel="alternate" type="application/rss+xml"
    href="/news/rss.xml"></head></html>"""
    feed = b"""<rss><channel><item><title>Discovered article</title>
    <link>https://example.com/article</link><description>Summary</description>
    </item></channel></rss>"""

    with patch(
        "app.fetcher.download",
        side_effect=[
            (endpoint.url, page, "text/html"),
            ("https://example.com/news/rss.xml", feed, "application/rss+xml"),
        ],
    ):
        run = fetch_endpoint(db_session, endpoint)

    assert run.status == "succeeded"
    assert run.items_created == 1
    assert endpoint.url == "https://example.com/news/rss.xml"


def test_download_feed_tries_bounded_candidates_and_rejects_invalid_content():
    page = b"<html><head></head></html>"
    with patch(
        "app.fetcher.download",
        side_effect=[
            ("https://example.com/news", page, "text/html"),
            *(ValueError("not found") for _ in range(6)),
        ],
    ) as mocked_download:
        with pytest.raises(ValueError, match="No valid RSS or Atom"):
            download_feed("https://example.com/news", 20)

    assert 1 < mocked_download.call_count <= 7


def test_download_feed_recovers_broken_url_from_source_homepage():
    homepage = b"""<html><head><link rel="alternate" type="application/atom+xml"
    href="/atom.xml"></head></html>"""
    feed = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Recovered</title>
    <link href="https://example.com/article"/><summary>Summary</summary></entry></feed>"""
    with patch(
        "app.fetcher.download",
        side_effect=[
            ValueError("old feed unavailable"),
            ("https://example.com/", homepage, "text/html"),
            ("https://example.com/atom.xml", feed, "application/atom+xml"),
        ],
    ):
        final_url, items = download_feed(
            "https://example.com/old-feed.xml", 20, "https://example.com/"
        )

    assert final_url == "https://example.com/atom.xml"
    assert items[0].title == "Recovered"


def test_download_feed_uses_page_discovery_when_homepage_is_unavailable():
    page = b"""<html><head><link rel="alternate" type="application/rss+xml"
    href="/news/rss.xml"></head></html>"""
    feed = b"""<rss><channel><item><title>Discovered</title>
    <link>https://example.com/article</link></item></channel></rss>"""
    with patch(
        "app.fetcher.download",
        side_effect=[
            ("https://example.com/news/", page, "text/html"),
            ValueError("homepage unavailable"),
            ("https://example.com/news/rss.xml", feed, "application/rss+xml"),
        ],
    ):
        final_url, items = download_feed(
            "https://example.com/news/", 20, "https://example.com/"
        )

    assert final_url == "https://example.com/news/rss.xml"
    assert items[0].title == "Discovered"


def test_private_endpoint_is_rejected():
    with patch("app.fetcher.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 80))]):
        with pytest.raises(ValueError, match="non-public"):
            validate_public_url("http://internal.example")


def test_fetch_endpoint_persists_items_and_deduplicates(db_session):
    endpoint = add_endpoint(db_session)
    feed = b"""<rss><channel><item><title>Article</title>
    <link>https://example.com/article</link></item></channel></rss>"""

    with patch("app.fetcher.download", return_value=(endpoint.url, feed, "application/rss+xml")):
        first = fetch_endpoint(db_session, endpoint)
        second = fetch_endpoint(db_session, endpoint)

    assert first.status == "succeeded"
    assert first.items_created == 1
    assert second.items_created == 0
    assert db_session.query(ContentItem).count() == 1
    assert db_session.query(ContentItem).one().analysis_status == "pending"
    assert db_session.query(FetchRun).count() == 2


def test_fetch_endpoint_refreshes_summary_for_existing_url(db_session):
    endpoint = add_endpoint(db_session)
    initial_feed = b"""<rss><channel><item><title>Article</title>
    <link>https://example.com/article</link><description>Image caption</description>
    </item></channel></rss>"""
    updated_feed = b"""<rss><channel><item><title>Article</title>
    <link>https://example.com/article</link><description>Available later</description>
    </item></channel></rss>"""

    with patch(
        "app.fetcher.download",
        side_effect=[
            (endpoint.url, initial_feed, "application/rss+xml"),
            (endpoint.url, updated_feed, "application/rss+xml"),
        ],
    ):
        first = fetch_endpoint(db_session, endpoint)
        second = fetch_endpoint(db_session, endpoint)

    item = db_session.query(ContentItem).one()
    assert first.items_created == 1
    assert second.items_created == 0
    assert item.summary == "Available later"


def test_fetch_endpoint_enriches_missing_feed_summary_from_article_metadata(db_session):
    endpoint = add_endpoint(db_session)
    feed = b"""<rss><channel><item><title>Article without feed summary</title>
    <link>https://example.com/article</link></item></channel></rss>"""
    page = b"""<html><head><title>Article</title>
    <meta property="og:description" content="Article metadata summary"></head></html>"""

    with patch(
        "app.fetcher.download",
        side_effect=[
            (endpoint.url, feed, "application/rss+xml"),
            ("https://example.com/article", page, "text/html"),
        ],
    ):
        run = fetch_endpoint(db_session, endpoint)

    assert run.status == "succeeded"
    assert db_session.query(ContentItem).one().summary == "Article metadata summary"


def test_fetch_endpoint_deduplicates_same_title_from_different_urls(db_session):
    endpoint = add_endpoint(db_session)
    first_feed = b"""<rss><channel><item><title>Shared article</title>
    <link>https://publisher.example/original</link></item></channel></rss>"""
    syndicated_feed = b"""<rss><channel><item><title> shared ARTICLE </title>
    <link>https://syndicator.example/repost</link></item></channel></rss>"""

    with patch(
        "app.fetcher.download",
        return_value=(endpoint.url, first_feed, "application/rss+xml"),
    ):
        first = fetch_endpoint(db_session, endpoint)
    with patch(
        "app.fetcher.download",
        return_value=(endpoint.url, syndicated_feed, "application/rss+xml"),
    ):
        second = fetch_endpoint(db_session, endpoint)

    assert first.items_created == 1
    assert second.items_created == 0
    assert db_session.query(ContentItem).count() == 1
    assert db_session.query(ContentItem).one().url == "https://publisher.example/original"


def test_web_endpoints_with_same_title_and_different_urls_are_distinct(db_session):
    endpoint = add_endpoint(db_session)
    endpoint.endpoint_type = "web"
    db_session.commit()
    page = b"<html><title>Shared site title</title><p>Useful body</p></html>"

    with patch(
        "app.fetcher.download",
        side_effect=[
            ("https://example.com/first", page, "text/html"),
            ("https://example.com/second", page, "text/html"),
        ],
    ):
        first = fetch_endpoint(db_session, endpoint)
        second = fetch_endpoint(db_session, endpoint)

    assert first.items_created == 1
    assert second.items_created == 1
    assert db_session.query(ContentItem).count() == 2


def test_download_rejects_private_url_before_request():
    with patch("app.fetcher.validated_connection_url", side_effect=ValueError("blocked")):
        with pytest.raises(ValueError, match="blocked"):
            download("http://localhost/private")
