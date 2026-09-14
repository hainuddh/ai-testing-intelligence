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