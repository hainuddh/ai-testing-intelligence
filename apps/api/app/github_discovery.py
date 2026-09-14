"""GitHub 自动发现候选：Search 查询构建、结果解析、初始动量估计。

确定性逻辑，不依赖数据库；DB 落库由 service 层完成。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from app.github_momentum import Momentum, momentum_score


@dataclass(frozen=True)
class DiscoveredRepo:
    full_name: str
    description: str
    language: str | None
    topics: list[str]
    stars: int
    forks: int
    open_issues: int
    watchers: int
    created_at: datetime | None
    pushed_at: datetime | None
    html_url: str
    homepage: str | None
    license_name: str | None
    archived: bool


def build_discovery_queries(languages: list[str], stars_min: int = 20) -> list[str]:
    """为每种语言生成一条 Search 查询，按 stars 降序拉取候选。"""
    return [f"language:{lang} stars:>={stars_min}" for lang in languages]


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def repo_from_search_item(item: dict) -> DiscoveredRepo:
    license_obj = item.get("license") or {}
    license_name = license_obj.get("spdx_id") or license_obj.get("name")
    return DiscoveredRepo(
        full_name=item["full_name"],
        description=item.get("description") or "",
        language=item.get("language"),
        topics=list(item.get("topics") or []),
        stars=item.get("stargazers_count") or 0,
        forks=item.get("forks_count") or 0,
        open_issues=item.get("open_issues_count") or 0,
        watchers=item.get("watchers_count") or 0,
        created_at=_parse_dt(item.get("created_at")),
        pushed_at=_parse_dt(item.get("pushed_at")),
        html_url=item.get("html_url") or "",
        homepage=item.get("homepage"),
        license_name=license_name,
        archived=bool(item.get("archived")),
    )


def estimate_star_deltas(stars: int, created_at: datetime, now: datetime) -> tuple[int, int]:
    """无历史快照时，用「星数 / 年龄」线性估计 24h 与 7d 增量。"""
    age_days = max(0.5, (now - created_at).total_seconds() / 86400)
    daily = stars / age_days
    return int(daily), int(daily * 7)


def score_candidate(
    stars: int,
    star_delta_24h: int,
    star_delta_7d: int,
    pushed_at: datetime | None,
    now: datetime | None = None,
) -> Momentum:
    now = now or datetime.now(UTC)
    return momentum_score(stars, star_delta_24h, star_delta_7d, pushed_at, now)