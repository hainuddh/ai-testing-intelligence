from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import func, select

from app import source_exploration as exploration
from app.analyzer import TestingAnalysis
from app.fetcher import FetchedItem
from app.models import (
    ContentItem,
    Source,
    SourceCandidate,
    SourceEndpoint,
    SourceExplorationPreference,
    User,
)
from app.security import create_access_token, hash_password
from app.source_catalog import CatalogSource

NOW = datetime(2026, 10, 17, 2, tzinfo=UTC)


def preference(db, **changes):
    pref = SourceExplorationPreference(
        id=1,
        enabled=True,
        topics=["testing"],
        languages=["en"],
        excluded_terms=[],
        max_trial_sources=3,
        **changes,
    )
    db.add(pref)
    db.commit()
    return pref


def entries(count=5, start=0):
    return [
        FetchedItem(
            title=f"Testing guide {i}",
            url=f"https://qe.example/posts/{i}",
            summary="A practical testing guide",
            published_at=NOW,
        )
        for i in range(start, start + count)
    ]


@pytest.fixture
def network(monkeypatch):
    from app import web_discovery

    monkeypatch.setattr(web_discovery, "download", Mock(side_effect=ValueError("No web fixture")))
    monkeypatch.setattr(
        exploration,
        "CATALOG",
        (CatalogSource("Quality blog", "https://qe.example/", ("testing",), "en"),),
    )
    monkeypatch.setattr(exploration.settings, "analysis_api_base_url", "")
    monkeypatch.setattr(exploration.settings, "analysis_model", "")
    download = Mock(return_value=("https://qe.example/feed.xml", entries()))
    monkeypatch.setattr(exploration, "download_feed", download)
    return download


def candidate(db, **changes):
    values = dict(
        name="Quality blog",
        homepage_url="https://qe.example/",
        feed_url="https://qe.example/feed.xml",
        topics=["testing"],
        language="en",
        discovery_kind="catalog",
        evidence_url="https://qe.example/",
        reason="Matches testing",
        status="trial",
        samples=[],
        seen_urls=[],
        sample_count=0,
        relevant_count=0,
        analysis_count=0,
    )
    values.update(changes)
    result = SourceCandidate(**values)
    db.add(result)
    db.commit()
    return result


def headers(db, role="maintainer"):
    user = User(
        username=role, password_hash=hash_password("test-password"), role=role, is_active=True
    )
    db.add(user)
    db.commit()
    return {"Authorization": f"Bearer {create_access_token(user.id, role)}"}


def test_worker_discovers_samples_without_creating_formal_content(db_session, network):
    preference(db_session)
    exploration.run_source_exploration(db_session, now=NOW)
    found = db_session.scalar(select(SourceCandidate))
    assert found.sample_count == 5
    assert found.analysis_count == 0
    assert found.status == "trial"
    assert all(s["analysis_status"] == "pending" for s in found.samples)
    assert all(s["testing_value_score"] is None for s in found.samples)
    for model in (Source, SourceEndpoint, ContentItem):
        assert db_session.scalar(select(func.count()).select_from(model)) == 0
    exploration.run_source_exploration(db_session, now=NOW + timedelta(minutes=1))
    assert network.call_count == 1


def test_no_topics_disabled_and_active_lease_do_not_access_network(db_session, network):
    pref = preference(db_session)
    pref.topics = []
    db_session.commit()
    exploration.run_source_exploration(db_session, now=NOW)
    pref.topics = ["testing"]
    pref.enabled = False
    db_session.commit()
    exploration.run_source_exploration(db_session, now=NOW)
    pref.enabled = True
    pref.lease_until = NOW + timedelta(minutes=10)
    pref.lease_token = "someone-else"
    db_session.commit()
    exploration.run_source_exploration(db_session, now=NOW)
    network.assert_not_called()


def test_dismissed_candidates_and_existing_sources_are_not_rediscovered(db_session, network):
    preference(db_session)
    candidate(db_session, status="dismissed")
    exploration.run_source_exploration(db_session, now=NOW)
    network.assert_not_called()
    assert db_session.scalar(select(func.count()).select_from(SourceCandidate)) == 1


def test_cap_counts_trial_and_ready_and_never_promotes_automatically(db_session, network):
    pref = preference(db_session)
    pref.max_trial_sources = 1
    candidate(
        db_session,
        status="ready",
        sample_count=20,
        samples=[{"url": "x"}],
        next_check_at=NOW + timedelta(days=1),
    )
    db_session.commit()
    exploration.run_source_exploration(db_session, now=NOW)
    network.assert_not_called()
    assert db_session.scalar(select(func.count()).select_from(Source)) == 0


def test_repeated_articles_count_once_and_failure_backs_off_without_demotion(db_session, network):
    preference(db_session)
    exploration.run_source_exploration(db_session, now=NOW)
    exploration.run_source_exploration(db_session, now=NOW + timedelta(days=1))
    found = db_session.scalar(select(SourceCandidate))
    assert found.sample_count == 5
    network.side_effect = ValueError("untrusted secret upstream body")
    exploration.run_source_exploration(db_session, now=NOW + timedelta(days=2))
    db_session.refresh(found)
    assert found.status == "trial"
    assert found.consecutive_failures == 1
    assert "secret" not in found.last_error
    assert found.next_check_at.replace(tzinfo=UTC) > NOW + timedelta(days=2)


def test_real_analysis_is_budgeted_and_remains_isolated(db_session, network, monkeypatch):
    preference(db_session)
    monkeypatch.setattr(exploration.settings, "analysis_api_base_url", "https://llm.example/v1")
    monkeypatch.setattr(exploration.settings, "analysis_model", "test")
    analyze = Mock(
        return_value=TestingAnalysis(
            True,
            80,
            75,
            "Useful testing guide",
            "Evidence",
            ["UI tests"],
            ["Try with a small suite"],
            [],
            ["testing"],
        )
    )
    monkeypatch.setattr(exploration, "analyze_content", analyze)
    exploration.run_source_exploration(db_session, now=NOW)
    found = db_session.scalar(select(SourceCandidate))
    assert found.analysis_count == 5
    assert found.relevant_count == 5
    assert found.status == "ready"
    assert analyze.call_count == 5
    assert db_session.scalar(select(func.count()).select_from(ContentItem)) == 0


def test_preferences_validation_and_viewer_permissions(client, db_session):
    auth = headers(db_session)
    response = client.put(
        "/api/v1/source-exploration/preferences",
        headers=auth,
        json={
            "enabled": True,
            "topics": [" Testing ", "testing", ""],
            "languages": ["en"],
            "excluded_terms": [],
            "max_trial_sources": 3,
        },
    )
    assert response.status_code == 200
    assert response.json()["topics"] == ["Testing"]
    assert "scope_note" in response.json()
    assert (
        client.put(
            "/api/v1/source-exploration/preferences",
            headers=auth,
            json={
                "max_trial_sources": 0,
            },
        ).status_code
        == 422
    )
    viewer = headers(db_session, "viewer")
    assert (
        client.put(
            "/api/v1/source-exploration/preferences", headers=viewer, json={"topics": ["testing"]}
        ).status_code
        == 403
    )
    assert client.get("/api/v1/source-exploration/candidates", headers=viewer).status_code == 200


def test_manual_confirmation_creates_one_source_and_endpoint_only(client, db_session, network):
    preference(db_session)
    exploration.run_source_exploration(db_session, now=NOW)
    found = db_session.scalar(select(SourceCandidate))
    auth = headers(db_session)
    response = client.post(
        f"/api/v1/source-exploration/candidates/{found.id}/promote", headers=auth
    )
    assert response.status_code == 200
    assert response.json()["candidate"]["status"] == "promoted"
    source_id = response.json()["source_id"]
    source = db_session.get(Source, source_id)
    assert source.status == "active"
    assert source.trust_level == 3
    assert db_session.scalar(select(func.count()).select_from(SourceEndpoint)) == 1
    assert db_session.scalar(select(func.count()).select_from(ContentItem)) == 0
    repeated = client.post(
        f"/api/v1/source-exploration/candidates/{found.id}/promote", headers=auth
    )
    assert repeated.status_code == 200
    assert repeated.json()["source_id"] == source_id


def test_paused_or_dismissed_candidate_cannot_be_promoted(client, db_session):
    preference(db_session)
    found = candidate(db_session, status="paused", sample_count=5)
    auth = headers(db_session)
    response = client.post(
        f"/api/v1/source-exploration/candidates/{found.id}/promote", headers=auth
    )
    assert response.status_code == 409
    resumed = client.post(
        f"/api/v1/source-exploration/candidates/{found.id}/action",
        headers=auth,
        json={"action": "resume"},
    )
    assert resumed.status_code == 200
    dismissed = client.post(
        f"/api/v1/source-exploration/candidates/{found.id}/action",
        headers=auth,
        json={"action": "dismiss"},
    )
    assert dismissed.json()["status"] == "dismissed"


def test_network_does_not_hold_database_transaction(db_session, network):
    preference(db_session)

    def downloaded(*args):
        assert not db_session.in_transaction()
        return "https://qe.example/feed.xml", entries()

    network.side_effect = downloaded
    exploration.run_source_exploration(db_session, now=NOW)


def test_preference_change_during_network_cancels_stale_results(db_session, network):
    preference(db_session)

    def downloaded(*args):
        pref = db_session.get(SourceExplorationPreference, 1)
        pref.enabled = False
        db_session.commit()
        return "https://qe.example/feed.xml", entries()

    network.side_effect = downloaded
    exploration.run_source_exploration(db_session, now=NOW)
    assert db_session.scalar(select(func.count()).select_from(SourceCandidate)) == 0


def test_failed_catalog_entries_do_not_starve_later_sources(db_session, network, monkeypatch):
    preference(db_session)
    monkeypatch.setattr(
        exploration,
        "CATALOG",
        tuple(
            CatalogSource(f"Testing {i}", f"https://catalog{i}.example/", ("testing",), "en")
            for i in range(8)
        ),
    )

    def download(url, *args):
        if any(f"catalog{i}.example" in url for i in range(6)):
            raise ValueError("Unavailable")
        return url + "feed", entries()

    network.side_effect = download
    exploration.run_source_exploration(db_session, now=NOW)
    assert network.call_count == 6
    exploration.run_source_exploration(db_session, now=NOW + timedelta(days=1))
    assert any("catalog6.example" in call.args[0] for call in network.call_args_list)
    assert db_session.scalar(select(SourceCandidate)) is not None


def test_completed_sample_budget_stops_extra_requests(db_session, network):
    preference(db_session)
    candidate(db_session, status="ready", sample_count=20, analysis_count=20, next_check_at=NOW)
    exploration.run_source_exploration(db_session, now=NOW)
    network.assert_not_called()


def test_lowering_trial_cap_pauses_overflow_without_losing_samples(client, db_session):
    preference(db_session)
    first = candidate(db_session, sample_count=1, samples=[{"url": "https://qe.example/one"}])
    second = candidate(
        db_session,
        feed_url="https://other.example/feed",
        sample_count=1,
        samples=[{"url": "https://other.example/one"}],
    )
    auth = headers(db_session)
    response = client.put(
        "/api/v1/source-exploration/preferences", headers=auth, json={"max_trial_sources": 1}
    )
    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(SourceCandidate, first.id).status == "trial"
    assert db_session.get(SourceCandidate, second.id).status == "paused"
    assert db_session.get(SourceCandidate, second.id).samples


def test_promotion_keeps_existing_same_named_sources(client, db_session, network):
    found = candidate(db_session, sample_count=1, samples=[{"url": "https://qe.example/one"}])
    auth = headers(db_session)
    for name in (found.name, f"{found.name} · {found.id}"):
        db_session.add(Source(name=name, source_type="website", created_by=1))
    db_session.commit()
    response = client.post(
        f"/api/v1/source-exploration/candidates/{found.id}/promote", headers=auth
    )
    assert response.status_code == 200
    assert db_session.scalar(select(func.count()).select_from(Source)) == 3
    assert len(set(db_session.scalars(select(Source.name)))) == 3


def test_source_topics_describe_the_source_separately_from_user_focus(
    db_session, network, monkeypatch
):
    pref = preference(db_session)
    pref.topics = ["AI辅助测试"]
    db_session.commit()
    monkeypatch.setattr(
        exploration,
        "CATALOG",
        (CatalogSource("AI engineering", "https://qe.example/", ("agents", "llm"), "en"),),
    )
    exploration.run_source_exploration(db_session, now=NOW)
    found = db_session.scalar(select(SourceCandidate))
    assert found.topics == ["agents", "llm"]
    assert "AI辅助测试" in found.reason


def test_focus_normalization_preserves_first_spelling_and_order(client, db_session):
    auth = headers(db_session)
    response = client.put(
        "/api/v1/source-exploration/preferences",
        headers=auth,
        json={"topics": [" Agents ", "testing", "agents", ""]},
    )
    assert response.status_code == 200
    assert response.json()["topics"] == ["Agents", "testing"]
