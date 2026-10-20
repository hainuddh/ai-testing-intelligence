"""Confirming a discovery admits its website and probes sustainable endpoints."""

from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app import source_exploration as exploration
from app import web_discovery
from app.fetcher import FetchedItem
from app.models import ContentItem, Source, SourceCandidate, SourceEndpoint, User

HOME = "https://lab.example/"
ARTICLE = HOME + "posts/testing-agents"
FEED = HOME + "feed.xml"


@pytest.fixture
def admission(db_session, monkeypatch):
    owner = User(username="site-owner", password_hash="unused", role="maintainer")
    db_session.add(owner)
    db_session.flush()
    candidate = SourceCandidate(
        name="An article about testing agents",
        homepage_url=HOME,
        feed_url=ARTICLE,
        endpoint_type="article",
        topics=["testing"],
        language="en",
        discovery_kind="web_link",
        evidence_url="https://seed.example/post",
        reason="Matches testing",
        sample_count=1,
        samples=[
            {
                "title": "Testing agents",
                "url": ARTICLE,
                "summary": "Practical evidence",
                "body": "A saved article",
                "analysis_status": "pending",
            }
        ],
        seen_urls=[ARTICLE],
    )
    db_session.add(candidate)
    db_session.commit()

    def feed(*args):
        assert not db_session.in_transaction()
        return FEED, [FetchedItem(title="Testing", url=ARTICLE)]

    def listing(*args):
        assert not db_session.in_transaction()
        return [FetchedItem(title="Testing", url=ARTICLE)]

    probe = Mock(side_effect=feed)
    reader = Mock(side_effect=listing)
    monkeypatch.setattr(exploration, "download_feed", probe)
    monkeypatch.setattr(web_discovery, "read_listing", reader)
    return candidate, owner.id, probe, reader


def test_article_confirmation_creates_website_rss_and_keeps_article_isolated(db_session, admission):
    candidate, owner_id, probe, reader = admission
    result = exploration.promote_candidate(db_session, candidate.id, owner_id)
    source = db_session.get(Source, result["source_id"])
    endpoint = db_session.scalar(select(SourceEndpoint))
    assert source.homepage_url == HOME and source.status == "active"
    assert source.name == "lab.example"
    assert endpoint.source_id == source.id and endpoint.endpoint_type == "rss"
    assert endpoint.url == FEED
    assert db_session.scalar(select(func.count()).select_from(ContentItem)) == 0
    assert probe.call_args.args[0] == HOME
    reader.assert_not_called()
    calls = probe.call_count
    repeated = exploration.promote_candidate(db_session, candidate.id, owner_id)
    assert repeated["source_id"] == source.id and probe.call_count == calls


def test_no_rss_admits_the_website_using_its_verified_listing(db_session, admission):
    candidate, owner_id, probe, reader = admission
    probe.side_effect = ValueError("No verified feed")
    result = exploration.promote_candidate(db_session, candidate.id, owner_id)
    endpoint = db_session.scalar(select(SourceEndpoint))
    assert endpoint.url == HOME and endpoint.endpoint_type == "web_listing"
    assert reader.call_args.args[0] == HOME
    assert db_session.get(Source, result["source_id"]).status == "active"
    assert db_session.scalar(select(func.count()).select_from(ContentItem)) == 0


def test_website_is_still_admitted_when_no_sustainable_endpoint_is_verified(db_session, admission):
    candidate, owner_id, probe, reader = admission
    probe.side_effect = ValueError("secret upstream details")
    reader.side_effect = ValueError("secret upstream details")
    result = exploration.promote_candidate(db_session, candidate.id, owner_id)
    source = db_session.get(Source, result["source_id"])
    assert source.status == "draft" and source.homepage_url == HOME
    assert "待配置" in source.description and "secret" not in source.description
    assert db_session.scalar(select(func.count()).select_from(SourceEndpoint)) == 0
    assert db_session.scalar(select(func.count()).select_from(ContentItem)) == 0
    assert candidate.status == "promoted"


@pytest.mark.parametrize("state", ["active", "paused", "archived"])
def test_same_website_reuses_existing_source_and_keeps_its_lifecycle(db_session, admission, state):
    candidate, owner_id, _probe, _reader = admission
    source = Source(
        name="Existing site",
        source_type="website",
        homepage_url=HOME.rstrip("/"),
        topics=["original"],
        status=state,
        created_by=owner_id,
    )
    db_session.add(source)
    db_session.commit()
    result = exploration.promote_candidate(db_session, candidate.id, owner_id)
    assert result["source_id"] == source.id
    assert db_session.scalar(select(func.count()).select_from(Source)) == 1
    assert source.name == "Existing site" and source.topics == ["original"]
    assert source.status == state


def test_an_installed_feed_is_reused_without_enabling_a_disabled_endpoint(db_session, admission):
    candidate, owner_id, _probe, _reader = admission
    source = Source(
        name="Existing feed owner",
        source_type="website",
        homepage_url=HOME,
        status="draft",
        created_by=owner_id,
    )
    db_session.add(source)
    db_session.flush()
    endpoint = SourceEndpoint(
        source_id=source.id, name="Paused feed", endpoint_type="rss", url=FEED, enabled=False
    )
    db_session.add(endpoint)
    db_session.commit()
    result = exploration.promote_candidate(db_session, candidate.id, owner_id)
    assert result["source_id"] == source.id
    assert not endpoint.enabled and source.status == "draft"
    assert db_session.scalar(select(func.count()).select_from(SourceEndpoint)) == 1


def test_two_articles_from_one_site_create_only_one_source_and_endpoint(db_session, admission):
    first, owner_id, _probe, _reader = admission
    first_result = exploration.promote_candidate(db_session, first.id, owner_id)
    second = SourceCandidate(
        name="Another testing article",
        homepage_url=HOME,
        feed_url=HOME + "posts/testing-apis",
        endpoint_type="article",
        topics=["testing"],
        discovery_kind="web_link",
        evidence_url=HOME,
        reason="Testing",
        sample_count=1,
        samples=[{"title": "Testing APIs", "url": HOME + "posts/testing-apis"}],
    )
    db_session.add(second)
    db_session.commit()
    second_result = exploration.promote_candidate(db_session, second.id, owner_id)
    assert second_result["source_id"] == first_result["source_id"]
    assert db_session.scalar(select(func.count()).select_from(Source)) == 1
    assert db_session.scalar(select(func.count()).select_from(SourceEndpoint)) == 1
    assert db_session.scalar(select(func.count()).select_from(ContentItem)) == 0


def test_web_listing_confirmation_also_prefers_a_discovered_feed(db_session, admission):
    candidate, owner_id, probe, reader = admission
    candidate.endpoint_type, candidate.feed_url = "web_listing", HOME
    db_session.commit()
    exploration.promote_candidate(db_session, candidate.id, owner_id)
    assert probe.called
    assert db_session.scalar(select(SourceEndpoint)).endpoint_type == "rss"
    reader.assert_not_called()


def test_candidate_paused_during_probe_cannot_publish_a_source(db_session, admission):
    candidate, owner_id, probe, _reader = admission

    def changed(*args):
        candidate.status = "paused"
        db_session.commit()
        return FEED, []

    probe.side_effect = changed
    with pytest.raises(HTTPException) as caught:
        exploration.promote_candidate(db_session, candidate.id, owner_id)
    assert caught.value.status_code == 409
    assert db_session.scalar(select(func.count()).select_from(Source)) == 0


def test_pause_or_dismiss_before_admission_does_not_probe(db_session, admission):
    candidate, owner_id, probe, reader = admission
    candidate.status = "dismissed"
    db_session.commit()
    with pytest.raises(HTTPException) as caught:
        exploration.promote_candidate(db_session, candidate.id, owner_id)
    assert caught.value.status_code == 409
    probe.assert_not_called()
    reader.assert_not_called()


def test_api_returns_website_admission_state_instead_of_article_admission(
    client, db_session, admission
):
    from app.security import create_access_token

    candidate, owner_id, probe, reader = admission
    probe.side_effect = ValueError("No feed")
    reader.return_value, reader.side_effect = [], None
    headers = {"Authorization": f"Bearer {create_access_token(owner_id, 'maintainer')}"}
    result = client.post(
        f"/api/v1/source-exploration/candidates/{candidate.id}/promote", headers=headers
    )
    assert result.status_code == 200
    assert result.json()["admission"]["status"] == "draft"
    assert "待配置" in result.json()["admission"]["message"]
    assert db_session.scalar(select(func.count()).select_from(ContentItem)) == 0


def test_invalid_homepage_is_rejected_without_network(db_session, admission):
    candidate, owner_id, probe, reader = admission
    candidate.homepage_url = "http://127.0.0.1/"
    db_session.commit()
    with pytest.raises(HTTPException) as caught:
        exploration.promote_candidate(db_session, candidate.id, owner_id)
    assert caught.value.status_code == 422
    probe.assert_not_called()
    reader.assert_not_called()


def test_rss_candidate_also_reuses_existing_website(db_session, admission):
    candidate, owner_id, _probe, _reader = admission
    candidate.endpoint_type, candidate.feed_url = "rss", FEED
    source = Source(
        name="Existing website",
        source_type="website",
        homepage_url=HOME,
        status="draft",
        created_by=owner_id,
    )
    db_session.add(source)
    db_session.commit()
    result = exploration.promote_candidate(db_session, candidate.id, owner_id)
    assert result["source_id"] == source.id
    assert db_session.scalar(select(func.count()).select_from(Source)) == 1
    assert source.status == "active"


def test_invalid_known_rss_is_not_installed_as_an_unverified_endpoint(db_session, admission):
    candidate, owner_id, probe, reader = admission
    candidate.endpoint_type, candidate.feed_url = "rss", FEED
    probe.side_effect = ValueError("No verified feed")
    db_session.commit()
    with pytest.raises(HTTPException) as caught:
        exploration.promote_candidate(db_session, candidate.id, owner_id)
    assert caught.value.status_code == 422
    assert db_session.scalar(select(func.count()).select_from(Source)) == 0
    reader.assert_not_called()


@pytest.mark.parametrize("kind", ["article", "rss"])
def test_verified_feed_query_is_preserved_exactly(db_session, admission, kind):
    candidate, owner_id, probe, _reader = admission
    candidate.endpoint_type = kind
    db_session.commit()
    url = FEED + "?utm_section=testing&signature=a%2fb"
    probe.side_effect, probe.return_value = None, (url, [])
    exploration.promote_candidate(db_session, candidate.id, owner_id)
    assert db_session.scalar(select(SourceEndpoint)).url == url


def test_domain_name_is_bounded_to_the_source_name_column(db_session, admission):
    candidate, owner_id, _probe, _reader = admission
    candidate.homepage_url = "https://" + ".".join(["a" * 50] * 4) + "/"
    db_session.commit()
    result = exploration.promote_candidate(db_session, candidate.id, owner_id)
    assert len(db_session.get(Source, result["source_id"]).name) <= 200
