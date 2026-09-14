"""GitHub REST API 客户端：发现候选与仓库详情。

无 token 直连公开 API；预留 `ATI_GITHUB_TOKEN`，非空时自动带 Bearer。
"""

from __future__ import annotations

import httpx

from app.config import settings

GITHUB_API_BASE = "https://api.github.com"
GITHUB_ACCEPT = "application/vnd.github+json"
GITHUB_USER_AGENT = "signal-atlas-github-radar/0.1"


def build_headers() -> dict[str, str]:
    headers = {
        "Accept": GITHUB_ACCEPT,
        "User-Agent": GITHUB_USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


def _get_json(client: httpx.Client, url: str, params: dict | None) -> dict:
    response = client.get(url, params=params, headers=build_headers())
    response.raise_for_status()
    return response.json()


def search_repositories(
    query: str,
    *,
    client: httpx.Client | None = None,
    page: int = 1,
    per_page: int = 100,
) -> dict:
    url = f"{GITHUB_API_BASE}/search/repositories"
    params = {"q": query, "sort": "stars", "order": "desc", "page": page, "per_page": per_page}
    if client is None:
        with httpx.Client(timeout=settings.fetch_timeout_seconds) as client:
            return _get_json(client, url, params)
    return _get_json(client, url, params)


def get_repository(full_name: str, *, client: httpx.Client | None = None) -> dict:
    url = f"{GITHUB_API_BASE}/repos/{full_name}"
    if client is None:
        with httpx.Client(timeout=settings.fetch_timeout_seconds) as client:
            return _get_json(client, url, None)
    return _get_json(client, url, None)