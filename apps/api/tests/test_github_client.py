from datetime import UTC, datetime

import httpx
import pytest

from app.config import settings
from app.github_client import build_headers, get_repository, search_repositories


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_repositories_sends_expected_query():
    captured: dict = {}

    def handler(request: httpx.Request):
        captured["url"] = str(request.url)
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"total_count": 1, "items": [{"full_name": "a/b"}]})

    result = search_repositories("language:python stars:>100", client=_client(handler))

    assert result["items"][0]["full_name"] == "a/b"
    assert "search/repositories" in captured["url"]
    assert captured["params"]["q"] == "language:python stars:>100"
    assert captured["params"]["sort"] == "stars"
    assert captured["params"]["order"] == "desc"


def test_search_repositories_raises_on_error_status():
    def handler(request: httpx.Request):
        return httpx.Response(403, json={"message": "rate limit"})

    with pytest.raises(httpx.HTTPStatusError):
        search_repositories("language:go", client=_client(handler))


def test_get_repository():
    def handler(request: httpx.Request):
        assert request.url.path == "/repos/owner/repo"
        return httpx.Response(200, json={"full_name": "owner/repo", "stargazers_count": 42})

    result = get_repository("owner/repo", client=_client(handler))
    assert result["stargazers_count"] == 42


def test_build_headers_without_token(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "")
    headers = build_headers()
    assert "Authorization" not in headers
    assert headers["Accept"].startswith("application/vnd.github")


def test_build_headers_with_token(monkeypatch):
    monkeypatch.setattr(settings, "github_token", "ghp_test")
    headers = build_headers()
    assert headers["Authorization"] == "Bearer ghp_test"


def test_search_repositories_allows_updated_sort_and_pagination():
    def handler(request: httpx.Request):
        assert dict(request.url.params) == {
            "q": "language:python",
            "sort": "updated",
            "order": "desc",
            "page": "3",
            "per_page": "30",
        }
        return httpx.Response(200, json={"items": []})

    search_repositories(
        "language:python", client=_client(handler), sort="updated", page=3, per_page=30
    )


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Retry-After": "120"}, 120),
        ({"X-RateLimit-Reset": "1768003500"}, 300),
        ({"Retry-After": "120", "X-RateLimit-Reset": "1768003500"}, 300),
        ({"Retry-After": "Sat, 10 Jan 2026 00:02:00 GMT"}, 120),
        ({"Retry-After": "bad", "X-RateLimit-Reset": "bad"}, 60),
        ({}, 60),
    ],
)
def test_rate_limit_retry_delay_respects_response_headers(headers, expected):
    from app.github_client import retry_after_seconds

    request = httpx.Request("GET", "https://api.github.com/search/repositories")
    response = httpx.Response(429, headers=headers, request=request)
    error = httpx.HTTPStatusError("rate limited", request=request, response=response)
    assert retry_after_seconds(error, now=datetime(2026, 1, 10, tzinfo=UTC)) == expected
