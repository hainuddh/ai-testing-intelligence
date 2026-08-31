from datetime import UTC, datetime

from app.models import ContentItem, Source
from app.reporting import render_markdown_report


def test_report_blocks_markdown_and_unsafe_url_injection():
    source = Source(name="Source", source_type="rss", languages=["en"], topics=[], created_by=1)
    item = ContentItem(
        source=source,
        source_id=1,
        title="# Forged heading",
        url="https://example.com/article\n<img src=x onerror=alert(1)>",
        fetched_at=datetime(2026, 8, 30, tzinfo=UTC),
        analysis_summary="---",
        testing_value_analysis="> forged quote",
        applicable_scenarios=["[malicious](javascript:alert(1))"],
    )

    report = render_markdown_report([item], datetime(2026, 8, 30, tzinfo=UTC))

    assert "## 1. \\# Forged heading" in report
    assert "\n\\---\n" in report
    assert "\n&gt; forged quote\n" in report
    assert "\\[malicious\\]" in report
    assert "<img" not in report
    assert "无有效 HTTP/HTTPS 链接" in report


def test_report_rejects_invalid_idna_hostname():
    source = Source(name="Source", source_type="rss", languages=["en"], topics=[], created_by=1)
    item = ContentItem(
        source=source,
        source_id=1,
        title="Testing report",
        url=f"https://{'a' * 64}.example.com/article",
        fetched_at=datetime(2026, 8, 30, tzinfo=UTC),
    )

    report = render_markdown_report([item], datetime(2026, 8, 30, tzinfo=UTC))

    assert "无有效 HTTP/HTTPS 链接" in report
