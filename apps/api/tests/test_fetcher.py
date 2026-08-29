from unittest.mock import patch

import pytest

from app.fetcher import download, fetch_endpoint, parse_feed, parse_web, validate_public_url
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
    <rss><channel><item><title>New release</title><link>/article</link>
    <description>Release summary</description></item></channel></rss>"""

    items = parse_feed(feed, "https://example.com/feed.xml", 10)
    page = parse_web(b"<html><title>Page title</title><p>Useful body</p></html>", "https://example.com")

    assert items[0].url == "https://example.com/article"
    assert items[0].summary == "Release summary"
    assert page[0].title == "Page title"
    assert "Useful body" in (page[0].body or "")


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
    assert db_session.query(FetchRun).count() == 2


def test_download_rejects_private_url_before_request():
    with patch("app.fetcher.validated_connection_url", side_effect=ValueError("blocked")):
        with pytest.raises(ValueError, match="blocked"):
            download("http://localhost/private")
