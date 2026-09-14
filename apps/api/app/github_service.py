"""GitHub 追踪 service 层：编排自动发现、状态流转、日报生成。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.github_client import search_repositories
from app.github_discovery import (
    build_discovery_queries,
    estimate_star_deltas,
    repo_from_search_item,
    score_candidate,
)
from app.github_momentum import Momentum
from app.models import GitHubRepo, GitHubReport, GitHubReportItem


def upsert_repo(db: Session, repo, momentum: Momentum, *, now: datetime) -> GitHubRepo:
    obj = db.scalar(select(GitHubRepo).where(GitHubRepo.full_name == repo.full_name))
    if obj is None:
        obj = GitHubRepo(full_name=repo.full_name, status="discovered", first_seen_at=now)
        db.add(obj)

    obj.description = repo.description
    obj.html_url = repo.html_url
    obj.primary_language = repo.language
    obj.topics = repo.topics
    obj.homepage = repo.homepage
    obj.license_name = repo.license_name
    obj.archived = repo.archived
    obj.stars = repo.stars
    obj.forks = repo.forks
    obj.open_issues = repo.open_issues
    obj.watchers = repo.watchers
    obj.repo_created_at = repo.created_at
    obj.repo_pushed_at = repo.pushed_at
    obj.momentum_score = momentum.score
    obj.momentum_tier = momentum.tier
    obj.last_seen_at = now
    return obj


def discover_candidates(
    db: Session,
    languages: list[str],
    *,
    stars_min: int = 20,
    per_language: int = 30,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> int:
    """通过 GitHub Search API 自动发现候选并 upsert，返回处理的候选数量。"""
    now = now or datetime.now(UTC)
    count = 0
    for query in build_discovery_queries(languages, stars_min):
        try:
            payload = search_repositories(query, client=client, per_page=per_language)
        except httpx.HTTPError:
            continue
        for item in payload.get("items") or []:
            repo = repo_from_search_item(item)
            created_at = repo.created_at or now
            d24, d7 = estimate_star_deltas(repo.stars, created_at, now)
            momentum = score_candidate(repo.stars, d24, d7, repo.pushed_at, now)
            upsert_repo(db, repo, momentum, now=now)
            count += 1
    db.commit()
    return count


def set_repo_status(db: Session, repo_id: int, status: str) -> GitHubRepo | None:
    obj = db.get(GitHubRepo, repo_id)
    if obj is None:
        return None
    obj.status = status
    db.commit()
    return obj


def discovery_due(
    db: Session, *, now: datetime | None = None, interval_hours: int = 24
) -> bool:
    """判断是否该再次触发自动发现（距上次发现超过 interval_hours，或从未发现过）。"""
    now = now or datetime.now(UTC)
    latest = db.scalar(select(func.max(GitHubRepo.last_seen_at)))
    if latest is None:
        return True
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=UTC)
    return now - latest >= timedelta(hours=interval_hours)


def render_daily_markdown(title: str, repos: list[GitHubRepo]) -> str:
    lines = [f"# {title}", ""]
    for repo in repos:
        tier = repo.momentum_tier or "watch"
        language = repo.primary_language or "-"
        lines.append(f"## {repo.full_name}")
        lines.append(f"- 星数 {repo.stars} · 语言 {language} · 档位 {tier}")
        summary = repo.summary or repo.description or ""
        if summary:
            lines.append(f"- {summary}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate_daily_report(
    db: Session, *, now: datetime | None = None, top_n: int = 20
) -> GitHubReport:
    now = now or datetime.now(UTC)
    repos = list(
        db.scalars(
            select(GitHubRepo)
            .where(GitHubRepo.status.in_(["watched", "tracked"]))
            .order_by(GitHubRepo.momentum_score.desc())
            .limit(top_n)
        ).all()
    )

    report = GitHubReport(
        report_type="daily",
        title=f"GitHub 项目日报 {now:%Y-%m-%d}",
        period_start=now - timedelta(days=1),
        period_end=now,
        status="ready",
        generated_at=now,
    )
    db.add(report)
    db.flush()

    for rank, repo in enumerate(repos, 1):
        report.items.append(
            GitHubReportItem(
                repo_id=repo.id,
                rank=rank,
                momentum_score=repo.momentum_score,
                momentum_tier=repo.momentum_tier,
                highlight=repo.summary,
            )
        )

    report.body_markdown = render_daily_markdown(report.title, repos)
    db.commit()
    db.refresh(report)
    return report