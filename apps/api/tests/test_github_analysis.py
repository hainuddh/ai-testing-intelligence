import httpx
import pytest

from app.config import settings
from app.github_analysis import (
    RepoIntel,
    analyze_repo_intel,
    build_repo_intel_data,
    parse_repo_intel,
)


def test_build_repo_intel_data_includes_fields():
    data = build_repo_intel_data(
        "a/b", "desc", "Python", ["ai"], 100, "https://a.dev", "https://github.com/a/b"
    )
    assert '"full_name": "a/b"' in data
    assert '"html_url": "https://github.com/a/b"' in data
    assert '"homepage": "https://a.dev"' in data
    assert '"stars": 100' in data


def test_parse_repo_intel_valid_json():
    raw = (
        '{"summary": "一个测试编排框架", "testing_value_analysis": "可用于自动化回归", '
        '"applicable_scenarios": ["回归测试", "CI 集成"], "adoption_suggestions": ["先小范围试点"], '
        '"testing_value_score": 82, "tags": ["testing", "automation"]}'
    )
    i = parse_repo_intel(raw)
    assert i == RepoIntel(
        summary="一个测试编排框架",
        testing_value_analysis="可用于自动化回归",
        applicable_scenarios=["回归测试", "CI 集成"],
        adoption_suggestions=["先小范围试点"],
        testing_value_score=82,
        tags=["testing", "automation"],
    )


def test_parse_repo_intel_strips_markdown_fence():
    raw = '```json\n{"summary": "x", "testing_value_analysis": "y", "testing_value_score": 60}\n```'
    i = parse_repo_intel(raw)
    assert i.summary == "x"
    assert i.testing_value_score == 60
    assert i.applicable_scenarios == []


def test_parse_repo_intel_requires_summary():
    with pytest.raises(ValueError):
        parse_repo_intel('{"summary": "", "testing_value_analysis": "y"}')


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_analyze_repo_intel_calls_model(monkeypatch):
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
                            "content": '{"summary": "s", "testing_value_analysis": "t", '
                            '"applicable_scenarios": ["a"], "adoption_suggestions": ["b"], '
                            '"testing_value_score": 70, "tags": ["x"]}'
                        }
                    }
                ]
            },
        )

    i = analyze_repo_intel(
        "a/b", "d", "Go", [], 10, None, "https://github.com/a/b", client=_client(handler)
    )
    assert i.summary == "s"
    assert i.testing_value_score == 70
    assert i.adoption_suggestions == ["b"]


def test_analyze_repo_intel_requires_config(monkeypatch):
    monkeypatch.setattr(settings, "analysis_api_base_url", "")
    with pytest.raises(RuntimeError):
        analyze_repo_intel("a/b", None, None, [], 0, None, "https://github.com/a/b")