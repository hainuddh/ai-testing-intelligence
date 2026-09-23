import json
from unittest.mock import patch

from app.analyzer import TestingAnalysis as AnalysisResult
from app.analyzer import (
    analyze_content,
    analyze_pending,
    apply_analysis,
    enrich_pending_related_links,
    parse_analysis,
)
from app.models import ContentItem, Source, User
from app.security import hash_password


def add_content(db_session):
    user = User(
        username="analyst-owner",
        password_hash=hash_password("password"),
        role="admin",
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    source = Source(
        name="Testing source",
        source_type="rss",
        languages=["en"],
        topics=[],
        created_by=user.id,
    )
    db_session.add(source)
    db_session.flush()
    item = ContentItem(
        source_id=source.id,
        title="AI agents for regression testing",
        url="https://example.com/testing-agent",
        summary="A technique for generating and maintaining regression tests.",
    )
    db_session.add(item)
    db_session.commit()
    db_session.refresh(item)
    return item


def sample_analysis(score=85):
    return AnalysisResult(
        is_testing_relevant=True,
        testing_relevance_score=score,
        testing_value_score=90,
        analysis_summary="该技术用于维护回归测试。",
        testing_value_analysis="可降低用例维护成本，但需要人工审查。",
        applicable_scenarios=["回归测试用例维护"],
        adoption_suggestions=["先在非关键模块进行对照试验"],
        risks=["可能生成错误断言"],
        tags=["AI Agent", "回归测试"],
    )


def test_parse_and_apply_testing_analysis(db_session, monkeypatch):
    item = add_content(db_session)
    parsed = parse_analysis(json.dumps(sample_analysis().__dict__, ensure_ascii=False))
    monkeypatch.setattr("app.analyzer.settings.analysis_model", "test-model")

    apply_analysis(item, parsed)

    assert item.analysis_status == "analyzed"
    assert item.analysis_disposition == "radar"
    assert item.filter_reason is None
    assert item.testing_value_score == 90
    assert item.applicable_scenarios == ["回归测试用例维护"]


def test_low_relevance_content_is_filtered(db_session, monkeypatch):
    item = add_content(db_session)
    monkeypatch.setattr("app.analyzer.settings.testing_relevance_threshold", 60)

    apply_analysis(item, sample_analysis(score=40))

    assert item.analysis_status == "analyzed"
    assert item.analysis_disposition == "watch"
    assert item.filter_reason == "relevance_below_radar_threshold"


def test_irrelevant_and_low_value_content_records_filter_reason(db_session):
    irrelevant = add_content(db_session)
    irrelevant_analysis = sample_analysis()
    irrelevant_analysis.is_testing_relevant = False

    apply_analysis(irrelevant, irrelevant_analysis)

    assert irrelevant.analysis_status == "analyzed"
    assert irrelevant.analysis_disposition == "filtered"
    assert irrelevant.filter_reason == "model_irrelevant"

    low_value = ContentItem(
        source_id=irrelevant.source_id,
        title="Low value signal",
        url="https://example.com/low-value",
    )
    db_session.add(low_value)
    db_session.flush()
    low_value_analysis = sample_analysis()
    low_value_analysis.testing_value_score = 25

    apply_analysis(low_value, low_value_analysis)

    assert low_value.analysis_disposition == "filtered"
    assert low_value.filter_reason == "value_below_watch_threshold"


def test_analyze_pending_persists_result(db_session, monkeypatch):
    item = add_content(db_session)
    monkeypatch.setattr("app.analyzer.settings.analysis_api_base_url", "https://model.example/v1")
    monkeypatch.setattr("app.analyzer.settings.analysis_model", "test-model")
    monkeypatch.setattr("app.analyzer.settings.analysis_batch_size", 10)

    with patch("app.analyzer.analyze_content", return_value=sample_analysis()):
        analyzed, failed = analyze_pending(db_session)

    db_session.refresh(item)
    assert (analyzed, failed) == (1, 0)
    assert item.analysis_status == "analyzed"
    assert item.analysis_attempts == 1


def test_low_value_analysis_does_not_fetch_related_links(db_session, monkeypatch):
    item = add_content(db_session)
    analysis = sample_analysis()
    analysis.testing_value_score = 45
    monkeypatch.setattr("app.analyzer.settings.analysis_api_base_url", "https://model.example/v1")
    monkeypatch.setattr("app.analyzer.settings.analysis_model", "test-model")

    with (
        patch("app.analyzer.analyze_content", return_value=analysis),
        patch("app.fetcher.download") as download,
    ):
        analyzed, failed = analyze_pending(db_session)
        links_enriched = enrich_pending_related_links(db_session)

    assert (analyzed, failed) == (1, 0)
    assert links_enriched == 0
    assert item.analysis_status == "analyzed"
    assert item.analysis_disposition == "watch"
    assert item.related_links == []
    download.assert_not_called()


def test_high_value_analysis_extracts_related_links(db_session, monkeypatch):
    item = add_content(db_session)
    page = b"""<main><a href="https://tools.example/regression?utm_source=article">
    Regression testing tool</a></main>"""
    monkeypatch.setattr("app.analyzer.settings.analysis_api_base_url", "https://model.example/v1")
    monkeypatch.setattr("app.analyzer.settings.analysis_model", "test-model")

    with (
        patch("app.analyzer.analyze_content", return_value=sample_analysis()),
        patch(
            "app.fetcher.download",
            return_value=(item.url, page, "text/html"),
        ) as download,
    ):
        analyzed, failed = analyze_pending(db_session)
        links_enriched = enrich_pending_related_links(db_session)

    assert (analyzed, failed) == (1, 0)
    assert links_enriched == 1
    assert item.related_links == [
        {"title": "Regression testing tool", "url": "https://tools.example/regression"}
    ]
    assert item.related_links_extracted_at is not None
    download.assert_called_once_with(item.url)


def test_existing_high_value_analysis_is_enriched_without_model_call(db_session, monkeypatch):
    item = add_content(db_session)
    item.analysis_status = "analyzed"
    item.testing_relevance_score = 85
    item.testing_value_score = 90
    item.analysis_summary = "回归测试基准。"
    item.testing_value_analysis = "可用于测试工具选型。"
    item.applicable_scenarios = ["回归测试"]
    item.analysis_tags = ["benchmark"]
    db_session.commit()
    monkeypatch.setattr("app.analyzer.settings.analysis_api_base_url", "")
    monkeypatch.setattr("app.analyzer.settings.analysis_model", "")
    page = b'<main><a href="https://tools.example/benchmark">Benchmark</a></main>'

    with (
        patch("app.analyzer.analyze_content") as model_call,
        patch("app.fetcher.download", return_value=(item.url, page, "text/html")),
    ):
        analyzed, failed = analyze_pending(db_session)
        links_enriched = enrich_pending_related_links(db_session)

    assert (analyzed, failed) == (0, 0)
    assert links_enriched == 1
    assert item.related_links == [
        {"title": "Benchmark", "url": "https://tools.example/benchmark"}
    ]
    model_call.assert_not_called()


def test_platform_content_marks_link_enrichment_complete_without_fetching(db_session):
    item = add_content(db_session)
    item.source.source_type = "wechat"
    item.analysis_status = "analyzed"
    item.testing_relevance_score = 90
    item.testing_value_score = 90
    db_session.commit()

    with patch("app.fetcher.download") as download:
        links_enriched = enrich_pending_related_links(db_session)

    assert links_enriched == 1
    assert item.related_links == []
    assert item.related_links_extracted_at is not None
    download.assert_not_called()


def test_content_without_keyword_signal_still_reaches_model(db_session, monkeypatch):
    item = add_content(db_session)
    item.title = "A new architecture for autonomous agents"
    item.summary = "The release changes how agents recover from tool failures."
    db_session.commit()
    monkeypatch.setattr("app.analyzer.settings.analysis_api_base_url", "https://model.example/v1")
    monkeypatch.setattr("app.analyzer.settings.analysis_model", "test-model")

    with patch("app.analyzer.analyze_content", return_value=sample_analysis()) as model_call:
        analyzed, failed = analyze_pending(db_session)

    db_session.refresh(item)
    assert (analyzed, failed) == (1, 0)
    assert item.analysis_status == "analyzed"
    assert item.analysis_disposition == "radar"
    model_call.assert_called_once_with(item)


def test_short_summary_fetches_full_content_before_analysis(db_session, monkeypatch):
    item = add_content(db_session)
    item.summary = "Brief release note."
    db_session.commit()
    monkeypatch.setattr("app.analyzer.settings.analysis_api_base_url", "https://model.example/v1")
    monkeypatch.setattr("app.analyzer.settings.analysis_model", "test-model")
    page = b"<main>Detailed regression testing evidence and rollout guidance.</main>"

    with (
        patch("app.fetcher.download", return_value=(item.url, page, "text/html")) as download,
        patch("app.analyzer._request_analysis", return_value=sample_analysis()) as request,
    ):
        result = analyze_content(item)

    assert result.testing_relevance_score == 85
    assert item.body == "Detailed regression testing evidence and rollout guidance."
    download.assert_called_once_with(item.url)
    request.assert_called_once_with(item)


def test_uncertain_summary_is_reanalyzed_after_fetching_full_content(db_session, monkeypatch):
    item = add_content(db_session)
    item.summary = "A" * 500
    item.source.trust_level = 3
    item.source.topics = []
    db_session.commit()
    uncertain = sample_analysis(score=50)
    uncertain.testing_value_score = 55
    page = b"<main>Concrete validation scenarios, benchmarks, and regression evidence.</main>"
    monkeypatch.setattr("app.analyzer.settings.analysis_api_base_url", "https://model.example/v1")
    monkeypatch.setattr("app.analyzer.settings.analysis_model", "test-model")

    with (
        patch("app.fetcher.download", return_value=(item.url, page, "text/html")) as download,
        patch(
            "app.analyzer._request_analysis", side_effect=[uncertain, sample_analysis()]
        ) as request,
    ):
        result = analyze_content(item)

    assert result.testing_relevance_score == 85
    download.assert_called_once_with(item.url)
    assert request.call_count == 2


def test_analysis_rejects_insecure_model_endpoint(db_session, monkeypatch):
    item = add_content(db_session)
    monkeypatch.setattr("app.analyzer.settings.analysis_api_base_url", "http://model.example/v1")
    monkeypatch.setattr("app.analyzer.settings.analysis_model", "test-model")

    try:
        analyze_content(item)
    except RuntimeError as exc:
        assert "HTTPS" in str(exc)
    else:
        raise AssertionError("Expected an insecure endpoint to be rejected")


def test_failed_analysis_uses_retry_backoff(db_session, monkeypatch):
    item = add_content(db_session)
    monkeypatch.setattr("app.analyzer.settings.analysis_api_base_url", "https://model.example/v1")
    monkeypatch.setattr("app.analyzer.settings.analysis_model", "test-model")

    with patch("app.analyzer.analyze_content", side_effect=RuntimeError("provider unavailable")):
        analyzed, failed = analyze_pending(db_session)

    db_session.refresh(item)
    assert (analyzed, failed) == (0, 1)
    assert item.analysis_status == "failed"
    assert item.next_analysis_at is not None
