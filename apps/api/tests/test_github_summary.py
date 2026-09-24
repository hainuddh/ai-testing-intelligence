import httpx
import pytest

from app.config import settings
from app.github_summary import (
    RepoSummary,
    build_repo_data,
    parse_repo_summary,
    summarize_repo,
)


def test_build_repo_data_includes_fields():
    data = build_repo_data("a/b", "desc", "Python", ["ai"], 100, 30)
    assert '"full_name": "a/b"' in data
    assert '"description": "desc"' in data
    assert '"star_delta_7d": 30' in data


def test_parse_repo_summary_valid_json():
    raw = '{"summary": "一个 AI 编排工具", "why_notable": "增长快", "category": "AI Agent"}'
    s = parse_repo_summary(raw)
    assert s == RepoSummary("一个 AI 编排工具", "增长快", "AI Agent")


def test_parse_repo_summary_strips_markdown_fence():
    raw = '```json\n{"summary": "x", "why_notable": "", "category": ""}\n```'
    s = parse_repo_summary(raw)
    assert s.summary == "x"
    assert s.why_notable == ""
    assert s.category == ""


def test_parse_repo_summary_requires_summary():
    with pytest.raises(ValueError):
        parse_repo_summary('{"summary": "", "why_notable": "y"}')


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_summarize_repo_calls_model(monkeypatch):
    monkeypatch.setattr(settings, "analysis_api_base_url", "https://example.com/v1")
    monkeypatch.setattr(settings, "analysis_model", "test-model")
    monkeypatch.setattr(settings, "analysis_api_key", "sk-test")

    def handler(request: httpx.Request):
        assert request.headers["Authorization"] == "Bearer sk-test"
        body = request.content.decode()
        assert "repo_data" in body
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"summary": "s", "why_notable": "w", "category": "c"}'
                        }
                    }
                ]
            },
        )

    s = summarize_repo("a/b", "d", "Go", [], 10, 5, client=_client(handler))
    assert s.summary == "s"
    assert s.category == "c"


def test_summarize_repo_requires_config(monkeypatch):
    monkeypatch.setattr(settings, "analysis_api_base_url", "")
    with pytest.raises(RuntimeError):
        summarize_repo("a/b", None, None, [], 0, None)
