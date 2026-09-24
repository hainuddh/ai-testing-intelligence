"""GitHub 追踪 service 层：编排自动发现、状态流转、日报生成。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.github_analysis import analyze_repo_intel
from app.github_client import search_repositories
from app.github_discovery import (
    build_discovery_queries,
    build_topic_queries,
    estimate_star_deltas,
    repo_from_search_item,
    score_candidate,
)
from app.github_momentum import Momentum
from app.models import GitHubPreference, GitHubRepo, GitHubReport, GitHubReportItem


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
    topics: list[str] | None = None,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> int:
    """通过 GitHub Search API 自动发现候选并 upsert，返回处理的候选数量。

    配置了关注主题时按主题（topic 精确 + 关键词模糊）发现；否则退回按语言发现。
    """
    now = now or datetime.now(UTC)
    queries = (
        build_topic_queries(topics, stars_min)
        if topics
        else build_discovery_queries(languages, stars_min)
    )
    count = 0
    for query in queries:
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


def _normalize_topics(topics: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for topic in topics:
        term = (topic or "").strip()
        if term and term not in seen:
            seen.add(term)
            result.append(term)
    return result


def get_topics(db: Session) -> list[str]:
    pref = db.scalar(select(GitHubPreference).limit(1))
    return list(pref.topics) if pref else []


def set_topics(db: Session, topics: list[str]) -> list[str]:
    cleaned = _normalize_topics(topics)
    pref = db.scalar(select(GitHubPreference).limit(1))
    if pref is None:
        pref = GitHubPreference(topics=cleaned)
        db.add(pref)
    else:
        pref.topics = cleaned
    db.commit()
    return cleaned


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


def ensure_repo_intel(
    db: Session, repo: GitHubRepo, *, client: httpx.Client | None = None
) -> bool:
    """确保仓库已有深度情报分析：已分析则复用，未配置或失败则降级跳过。"""
    if repo.intel_status == "analyzed" and repo.intel_summary:
        return True
    try:
        intel = analyze_repo_intel(
            repo.full_name,
            repo.description,
            repo.primary_language,
            repo.topics or [],
            repo.stars,
            repo.homepage,
            repo.html_url,
            client=client,
        )
    except Exception:
        repo.intel_status = "failed"
        return False
    repo.intel_summary = intel.summary
    repo.testing_value_analysis = intel.testing_value_analysis
    repo.applicable_scenarios = intel.applicable_scenarios
    repo.adoption_suggestions = intel.adoption_suggestions
    repo.testing_value_score = intel.testing_value_score
    repo.intel_status = "analyzed"
    return True


def render_daily_markdown(title: str, repos: list[GitHubRepo]) -> str:
    lines = [f"# {title}", ""]
    for repo in repos:
        tier = repo.momentum_tier or "watch"
        language = repo.primary_language or "-"
        lines.append(f"## {repo.full_name}")
        meta = [f"星数 {repo.stars}", f"语言 {language}", f"档位 {tier}"]
        if repo.testing_value_score is not None:
            meta.append(f"测试价值 {repo.testing_value_score}/100")
        lines.append("- " + " · ".join(meta))
        lines.append(f"- 仓库地址：{repo.html_url}")

        summary = repo.intel_summary or repo.summary or repo.description or ""
        if summary:
            lines.extend(["", "**项目摘要**", summary])
        if repo.testing_value_analysis:
            lines.extend(["", "**测试价值分析**", repo.testing_value_analysis])
        if repo.applicable_scenarios:
            lines.append("")
            lines.append("**应用场景推荐**")
            lines.extend(f"- {s}" for s in repo.applicable_scenarios)
        if repo.adoption_suggestions:
            lines.append("")
            lines.append("**落地建议**")
            lines.extend(f"- {s}" for s in repo.adoption_suggestions)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate_daily_report(
    db: Session,
    *,
    now: datetime | None = None,
    top_n: int = 20,
    client: httpx.Client | None = None,
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

    for repo in repos:
        ensure_repo_intel(db, repo, client=client)

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