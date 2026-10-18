"""Autonomous discovery uses real links, bounded requests and isolated evidence."""

from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select

from app import source_exploration as exploration
from app.fetcher import fetch_endpoint
from app.models import ContentItem, Source, SourceCandidate, SourceEndpoint, User

NOW = datetime(2026, 10, 18, 2, tzinfo=UTC)
HOME = "https://lab.example/"
ARTICLE = HOME + "posts/testing-agents"


@pytest.fixture
def web(db_session, monkeypatch):
    from app import web_discovery

    monkeypatch.setattr(exploration, "CATALOG", ())
    monkeypatch.setattr(exploration.settings, "analysis_model", "")
    monkeypatch.setattr(exploration.settings, "analysis_api_base_url", "")
    owner = User(username="owner", password_hash="unused", role="maintainer")
    db_session.add(owner)
    db_session.flush()
    source = Source(name="Existing", source_type="rss", created_by=owner.id, status="active")
    db_session.add(source)
    db_session.flush()
    db_session.add(
        ContentItem(
            source_id=source.id,
            title="Testing research",
            url="https://seed.example/post",
            analysis_status="analyzed",
            related_links=[{"title": "Testing lab", "url": HOME}],
        )
    )
    pref = exploration.preferences(db_session)
    pref.topics, pref.languages = ["testing"], ["en"]
    db_session.commit()
    pages = {
        HOME: (
            '<html lang="en"><title>Testing lab</title><main>'
            '<a href="/posts/testing-agents">Testing agents in production</a>'
            '<a href="/posts/testing-apis">Testing APIs</a></main></html>'
        ),
        ARTICLE: (
            '<html lang="en"><title>Testing agents in production</title>'
            "<article>Practical testing experience: reproduce failures and compare test "
            'coverage. <a href="https://other.example/testing">Testing browser lessons</a>'
            "</article></html>"
        ),
        HOME + "posts/testing-apis": (
            '<html lang="en"><title>Testing APIs</title>'
            "<article>Testing API workflows with reproducible "
            "assertions.</article></html>"
        ),
        "https://other.example/testing": (
            '<html lang="en"><title>Testing browser lessons</title>'
            "<article>Testing browser agents with real regression "
            "cases.</article></html>"
        ),
    }

    def download(url):
        assert not db_session.in_transaction(), "Network must not hold database transactions"
        if url.endswith("/robots.txt"):
            return url, b"User-agent: *\nAllow: /", "text/plain"
        if url not in pages:
            raise ValueError("Not found")
        return url, pages[url].encode(), "text/html"

    request = Mock(side_effect=download)
    monkeypatch.setattr(web_discovery, "download", request)
    monkeypatch.setattr(exploration, "download_feed", Mock(side_effect=ValueError("No feed")))
    return web_discovery, request, pages, owner.id


def test_worker_finds_no_feed_listing_and_articles_without_formal_rows(db_session, web):
    _module, request, _pages, _owner = web
    exploration.run_source_exploration(db_session, now=NOW)
    found = list(db_session.scalars(select(SourceCandidate)))
    assert any(c.endpoint_type == "web_listing" and c.feed_url == HOME for c in found)
    article = next(c for c in found if c.endpoint_type == "article" and c.feed_url == ARTICLE)
    assert article.samples[0]["body"]
    assert article.samples[0]["analysis_status"] == "pending"
    assert article.evidence_url == HOME
    assert db_session.scalar(select(func.count()).select_from(ContentItem)) == 1
    assert db_session.scalar(select(func.count()).select_from(SourceEndpoint)) == 0
    assert request.call_count <= _module.REQUEST_BUDGET
    assert any(c.feed_url == "https://other.example/testing" for c in found)


def test_discovery_is_durable_and_dismissed_article_is_not_requested_again(db_session, web):
    _module, request, _pages, _owner = web
    exploration.run_source_exploration(db_session, now=NOW)
    article = db_session.scalar(select(SourceCandidate).where(SourceCandidate.feed_url == ARTICLE))
    article.status = "dismissed"
    db_session.commit()
    request.reset_mock()
    exploration.run_source_exploration(db_session, now=NOW + timedelta(days=1))
    assert ARTICLE not in [call.args[0] for call in request.call_args_list]
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(SourceCandidate)
            .where(
                SourceCandidate.feed_url == ARTICLE,
            )
        )
        == 1
    )


def test_disabled_or_empty_focus_never_crawls(db_session, web):
    _module, request, _pages, _owner = web
    pref = exploration.preferences(db_session)
    pref.enabled = False
    db_session.commit()
    exploration.run_source_exploration(db_session, now=NOW)
    pref.enabled, pref.topics = True, []
    db_session.commit()
    exploration.run_source_exploration(db_session, now=NOW)
    request.assert_not_called()


def test_robots_denial_and_nonpublic_links_are_respected(db_session, web):
    module, request, pages, _owner = web
    pages[HOME] += '<a href="http://127.0.0.1/testing">Testing secrets</a>'
    original = request.side_effect

    def download(url):
        if url == HOME + "robots.txt":
            return url, b"User-agent: *\nDisallow: /", "text/plain"
        return original(url)

    request.side_effect = download
    exploration.run_source_exploration(db_session, now=NOW)
    assert HOME not in [call.args[0] for call in request.call_args_list]
    assert all("127.0.0.1" not in call.args[0] for call in request.call_args_list)
    assert db_session.scalar(select(func.count()).select_from(SourceCandidate)) == 0
    assert module.MAX_DEPTH <= 2


def test_scope_changed_during_request_drops_results(db_session, web):
    _module, request, _pages, _owner = web
    original = request.side_effect

    def download(url):
        result = original(url)
        if url == HOME:
            pref = exploration.preferences(db_session)
            pref.enabled = False
            db_session.commit()
        return result

    request.side_effect = download
    exploration.run_source_exploration(db_session, now=NOW)
    assert db_session.scalar(select(func.count()).select_from(SourceCandidate)) == 0


def test_article_approval_is_idempotent_without_recurring_endpoint(db_session, web):
    _module, _request, _pages, owner_id = web
    exploration.run_source_exploration(db_session, now=NOW)
    found = db_session.scalar(select(SourceCandidate).where(SourceCandidate.feed_url == ARTICLE))
    result = exploration.promote_candidate(db_session, found.id, owner_id)
    repeated = exploration.promote_candidate(db_session, found.id, owner_id)
    assert result["source_id"] == repeated["source_id"]
    assert (
        db_session.scalar(
            select(func.count())
            .select_from(ContentItem)
            .where(
                ContentItem.url == ARTICLE,
            )
        )
        == 1
    )
    assert db_session.scalar(select(func.count()).select_from(SourceEndpoint)) == 0


def test_listing_approval_creates_collectable_endpoint(db_session, web, monkeypatch):
    module, _request, _pages, owner_id = web
    exploration.run_source_exploration(db_session, now=NOW)
    found = db_session.scalar(select(SourceCandidate).where(SourceCandidate.feed_url == HOME))
    exploration.promote_candidate(db_session, found.id, owner_id)
    endpoint = db_session.scalar(select(SourceEndpoint))
    assert endpoint.endpoint_type == "web_listing"
    # Formal collection uses the same reader and stores article pages, not the index page.
    run = fetch_endpoint(db_session, endpoint)
    assert run.status == "succeeded"
    urls = list(
        db_session.scalars(
            select(ContentItem.url).where(ContentItem.source_id == endpoint.source_id)
        )
    )
    assert ARTICLE in urls and HOME not in urls
    assert found.check_count >= 1 and found.success_count >= 1


def test_sitemap_can_supply_real_links_without_an_html_listing(db_session, web):
    _module, request, pages, _owner = web
    pages[HOME] = '<html lang="en"><title>Testing lab</title><p>Testing engineering</p></html>'
    original = request.side_effect

    def download(url):
        if url == HOME + "robots.txt":
            return (
                url,
                b"User-agent: *\nAllow: /\nSitemap: https://lab.example/sitemap.xml",
                "text/plain",
            )
        if url == HOME + "sitemap.xml":
            return (
                url,
                f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{ARTICLE}</loc></url></urlset>'.encode(),
                "application/xml",
            )
        return original(url)

    request.side_effect = download
    exploration.run_source_exploration(db_session, now=NOW)
    assert db_session.scalar(select(SourceCandidate).where(SourceCandidate.feed_url == ARTICLE))


def test_reader_enforces_budget_and_does_not_follow_cross_site_redirect(monkeypatch):
    from app import web_discovery
    from app.fetcher import request_guard

    def redirect(url):
        guard = request_guard.get()
        guard(url)
        guard("https://unexpected.example/page")
        pytest.fail("Cross-site redirect must be rejected before the next request")

    monkeypatch.setattr(web_discovery, "download", redirect)
    with pytest.raises(ValueError, match="跨站"):
        web_discovery.Reader().get(HOME)

    request = Mock(return_value=(HOME, b"page", "text/html"))
    monkeypatch.setattr(web_discovery, "download", request)
    reader = web_discovery.Reader()
    for _ in range(web_discovery.PER_HOST_BUDGET):
        reader.get(HOME)
    with pytest.raises(web_discovery.BudgetExceeded):
        reader.get(HOME)
    assert request.call_count == web_discovery.PER_HOST_BUDGET


def test_manual_only_platforms_are_never_requested(db_session, web):
    _module, request, pages, _owner = web
    pages[ARTICLE] += '<a href="https://mp.weixin.qq.com/s/testing">Testing guide</a>'
    exploration.run_source_exploration(db_session, now=NOW)
    assert all("weixin.qq.com" not in call.args[0] for call in request.call_args_list)


def test_failed_trial_analysis_retries_from_saved_body_without_network(
    db_session, web, monkeypatch
):
    from app.analyzer import TestingAnalysis

    _module, request, _pages, _owner = web
    exploration.run_source_exploration(db_session, now=NOW)
    monkeypatch.setattr(exploration.settings, "analysis_api_base_url", "https://llm.example/v1")
    monkeypatch.setattr(exploration.settings, "analysis_model", "test")
    analyze = Mock(
        return_value=TestingAnalysis(
            True, 85, 80, "Useful", "Evidence", ["Regression"], ["Try"], [], []
        )
    )
    monkeypatch.setattr(exploration, "analyze_content", analyze)
    request.reset_mock()
    exploration.run_source_exploration(db_session, now=NOW + timedelta(days=1))
    article = db_session.scalar(select(SourceCandidate).where(SourceCandidate.feed_url == ARTICLE))
    assert article.analysis_count == 1 and article.status == "ready"
    assert ARTICLE not in [call.args[0] for call in request.call_args_list]


def test_api_exposes_kind_and_evidence_evaluation(client, db_session, web):
    from app.security import create_access_token

    _module, _request, _pages, owner_id = web
    exploration.run_source_exploration(db_session, now=NOW)
    auth = {"Authorization": f"Bearer {create_access_token(owner_id, 'maintainer')}"}
    response = client.get("/api/v1/source-exploration/candidates", headers=auth)
    assert response.status_code == 200
    article = next(row for row in response.json()["items"] if row["endpoint_type"] == "article")
    assert "单篇" in article["evaluation"]["recommendation"]
    assert "body" not in article["samples"][0], (
        "Bounded list responses must not expose full saved bodies"
    )


def test_listing_endpoint_is_editable_via_existing_api(client, db_session, web):
    from app.security import create_access_token

    _module, _request, _pages, owner_id = web
    exploration.run_source_exploration(db_session, now=NOW)
    found = db_session.scalar(select(SourceCandidate).where(SourceCandidate.feed_url == HOME))
    result = exploration.promote_candidate(db_session, found.id, owner_id)
    endpoint = db_session.scalar(select(SourceEndpoint))
    response = client.patch(
        f"/api/v1/sources/{result['source_id']}/endpoints/{endpoint.id}",
        headers={"Authorization": f"Bearer {create_access_token(owner_id, 'maintainer')}"},
        json={"endpoint_type": "web_listing", "name": "Updated listing"},
    )
    assert response.status_code == 200


def test_new_links_are_not_starved_by_repeated_homepage_checks(db_session, web, monkeypatch):
    module, request, _pages, _owner = web
    monkeypatch.setattr(module, "REQUEST_BUDGET", 4)
    exploration.run_source_exploration(db_session, now=NOW)
    assert ARTICLE not in [call.args[0] for call in request.call_args_list]
    request.reset_mock()
    exploration.run_source_exploration(db_session, now=NOW + timedelta(days=1))
    assert ARTICLE in [call.args[0] for call in request.call_args_list]


def test_read_failure_updates_visible_trial_health(db_session, web):
    _module, _request, pages, _owner = web
    exploration.run_source_exploration(db_session, now=NOW)
    trial = db_session.scalar(select(SourceCandidate).where(SourceCandidate.feed_url == HOME))
    before = trial.check_count
    del pages[HOME]
    exploration.run_source_exploration(db_session, now=NOW + timedelta(days=1))
    db_session.refresh(trial)
    assert trial.check_count > before and trial.success_count == before
    assert trial.last_error and trial.samples


def test_formal_source_pause_blocks_frontier_even_without_trailing_slash(db_session, web):
    _module, request, _pages, owner_id = web
    db_session.add(
        Source(
            name="Paused lab",
            source_type="website",
            homepage_url=HOME.rstrip("/"),
            created_by=owner_id,
            status="paused",
        )
    )
    db_session.commit()
    exploration.run_source_exploration(db_session, now=NOW)
    assert all(not call.args[0].startswith(HOME) for call in request.call_args_list)


def test_formal_listing_moves_past_already_collected_articles(db_session, web):
    _module, request, _pages, owner_id = web
    exploration.run_source_exploration(db_session, now=NOW)
    found = db_session.scalar(select(SourceCandidate).where(SourceCandidate.feed_url == HOME))
    exploration.promote_candidate(db_session, found.id, owner_id)
    endpoint = db_session.scalar(select(SourceEndpoint))
    fetch_endpoint(db_session, endpoint)
    request.reset_mock()
    second = fetch_endpoint(db_session, endpoint)
    assert second.status == "succeeded" and second.items_created == 0
    assert ARTICLE not in [call.args[0] for call in request.call_args_list]


def test_article_cards_on_a_homepage_are_a_listing_not_one_article(db_session, web):
    _module, _request, pages, _owner = web
    pages[HOME] = (
        '<html lang="en"><title>Testing lab</title><main>'
        '<article><a href="/posts/testing-agents">Testing agents</a></article>'
        '<article><a href="/posts/testing-apis">Testing APIs</a></article>'
        "</main></html>"
    )
    exploration.run_source_exploration(db_session, now=NOW)
    candidate = db_session.scalar(select(SourceCandidate).where(SourceCandidate.feed_url == HOME))
    assert candidate.endpoint_type == "web_listing"


def test_existing_rss_trial_does_not_gain_a_duplicate_listing(db_session, web):
    _module, _request, _pages, _owner = web
    db_session.add(
        SourceCandidate(
            name="RSS lab",
            homepage_url=HOME,
            feed_url=HOME + "feed.xml",
            endpoint_type="rss",
            topics=["testing"],
            language="en",
            discovery_kind="catalog",
            evidence_url=HOME,
            reason="Testing",
            samples=[],
            next_check_at=NOW + timedelta(days=2),
        )
    )
    db_session.commit()
    exploration.run_source_exploration(db_session, now=NOW)
    assert (
        db_session.scalar(
            select(SourceCandidate).where(
                SourceCandidate.feed_url == HOME,
                SourceCandidate.endpoint_type == "web_listing",
            )
        )
        is None
    )
    assert db_session.scalar(select(SourceCandidate).where(SourceCandidate.feed_url == ARTICLE))


def test_migration_preserves_existing_trials_and_defaults(tmp_path, monkeypatch):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    from app.config import settings

    db_url = f"sqlite:///{tmp_path / 'web-discovery-migration.db'}"
    monkeypatch.setattr(settings, "database_url", db_url)
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    previous = "20261017_01_source_exploration"
    command.upgrade(config, previous)
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO source_candidates (id,name,homepage_url,feed_url,topics,language,"
                    "discovery_kind,evidence_url,reason,status,samples,seen_urls,sample_count,"
                    "relevant_count,analysis_count,consecutive_failures,created_at,updated_at) "
                    "VALUES "
                    "(1,'Existing','https://example.com/','https://example.com/feed','[]','en',"
                    "'catalog','https://example.com/','Testing','trial','[]','[]',0,0,0,0,"
                    "CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                )
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert tuple(
                connection.execute(
                    text(
                        "SELECT endpoint_type,check_count,success_count "
                        "FROM source_candidates WHERE id=1"
                    )
                ).one()
            ) == ("rss", 0, 0)
        assert "source_discovery_pages" in inspect(engine).get_table_names()
        command.downgrade(config, previous)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT name FROM source_candidates WHERE id=1"))
                == "Existing"
            )
        assert "source_discovery_pages" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
    finally:
        engine.dispose()
