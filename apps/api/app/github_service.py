"""GitHub 追踪 service 层：编排自动发现、状态流转、日报生成。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.github_analysis import analyze_repo_intel
from app.models import (
    GitHubExplorationState,
    GitHubPreference,
    GitHubRepo,
    GitHubReport,
    GitHubReportItem,
)


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
    """Discover only new candidates; transaction ownership stays with the caller."""
    from app.github_search import discover_new_candidates

    return discover_new_candidates(
        db,
        languages,
        stars_min=stars_min,
        per_language=per_language,
        topics=topics,
        client=client,
        now=now,
    )


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


def discovery_due(db: Session, *, now: datetime | None = None, interval_hours: int = 24) -> bool:
    """Scheduling is independent of repository updates, including empty runs."""
    now = now or datetime.now(UTC)
    state = get_exploration_state(db)
    if state.status == "running":
        return state.started_at is None or _utc(state.started_at) <= now - LEASE_DURATION
    if state.next_run_at is None:
        return True
    return _utc(state.next_run_at) <= now


def ensure_repo_intel(db: Session, repo: GitHubRepo, *, client: httpx.Client | None = None) -> bool:
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


# Shanghai has no DST; a fixed offset also works without OS tzdata on Windows.
BUSINESS_TZ = timezone(timedelta(hours=8))
LEASE_DURATION = timedelta(hours=1)


class ExplorationLeaseLost(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or UTC)


def unreported_repo():
    return ~select(GitHubReportItem.id).where(GitHubReportItem.repo_id == GitHubRepo.id).exists()


def get_exploration_state(db: Session) -> GitHubExplorationState:
    state = db.get(GitHubExplorationState, 1, populate_existing=True)
    return state or GitHubExplorationState(
        id=1, status="never_run", discovered_count=0, search_cursors={}
    )


def claim_exploration(db: Session, *, now: datetime, force: bool = False) -> str | None:
    # A singleton compare-and-swap works across both API/worker processes and DBs.
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    db.execute(
        insert(GitHubExplorationState)
        .values(id=1, status="never_run", discovered_count=0, search_cursors={})
        .on_conflict_do_nothing(index_elements=["id"])
    )
    available = or_(
        GitHubExplorationState.status != "running",
        GitHubExplorationState.started_at <= now - LEASE_DURATION,
    )
    conditions = [GitHubExplorationState.id == 1, available]
    if not force:
        conditions.append(
            or_(
                GitHubExplorationState.next_run_at.is_(None),
                GitHubExplorationState.next_run_at <= now,
                GitHubExplorationState.status == "running",
            )
        )
    token = uuid4().hex
    result = db.execute(
        update(GitHubExplorationState)
        .where(*conditions)
        .values(
            status="running",
            started_at=now,
            finished_at=None,
            lease_token=token,
            discovered_count=0,
            report_id=None,
            error_message=None,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire_all()
    return token if result.rowcount else None


def finish_exploration(
    db: Session,
    token: str,
    *,
    now: datetime,
    report: GitHubReport | None,
    discovered: int,
    error: str | None = None,
    retry_seconds: int = 3600,
) -> None:
    # The conditional write locks the lease until publication commits. A stale
    # process must never publish results after a replacement has claimed it.
    with db.no_autoflush:
        result = db.execute(
            update(GitHubExplorationState)
            .where(
                GitHubExplorationState.id == 1,
                GitHubExplorationState.lease_token == token,
            )
            .values(
                status="failed" if error else "completed",
                finished_at=now,
                next_run_at=now
                + (timedelta(seconds=retry_seconds) if error else timedelta(hours=24)),
                discovered_count=discovered,
                error_message=error,
                lease_token=None,
            )
            .execution_options(synchronize_session=False)
        )
    if not result.rowcount:
        db.rollback()
        raise ExplorationLeaseLost("GitHub exploration was claimed by another worker")
    db.flush()
    db.execute(
        update(GitHubExplorationState)
        .where(GitHubExplorationState.id == 1)
        .values(report_id=report.id if report else None)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    db.expire_all()


def _report_for_day(db: Session, now: datetime) -> GitHubReport | None:
    day = now.astimezone(BUSINESS_TZ).date()
    # Include old reports without report_key to avoid republishing on upgrade day.
    start = datetime.combine(day, datetime.min.time(), tzinfo=BUSINESS_TZ).astimezone(UTC)
    return db.scalar(
        select(GitHubReport)
        .where(
            GitHubReport.report_type == "daily",
            or_(
                GitHubReport.report_key == f"daily:{day}",
                (GitHubReport.generated_at >= start)
                & (GitHubReport.generated_at < start + timedelta(days=1)),
            ),
        )
        .order_by(GitHubReport.generated_at.desc())
        .limit(1)
    )


def _matches_topics(repo: GitHubRepo, topics: list[str]) -> bool:
    text = " ".join([repo.full_name, repo.description or "", *(repo.topics or [])]).casefold()
    return not topics or any(topic.casefold() in text for topic in topics)


def _build_daily_report(
    db: Session,
    *,
    now: datetime,
    top_n: int = 5,
    client: httpx.Client | None = None,
) -> tuple[GitHubReport | None, bool]:
    existing = _report_for_day(db, now)
    if existing:
        return existing, False
    topics = get_topics(db)
    candidates = db.scalars(
        select(GitHubRepo)
        .where(
            GitHubRepo.status != "ignored",
            GitHubRepo.archived.is_(False),
            unreported_repo(),
            or_(
                GitHubRepo.intel_status != "analyzed",
                GitHubRepo.testing_value_score >= settings.testing_relevance_threshold,
            ),
        )
        .order_by(GitHubRepo.momentum_score.desc(), GitHubRepo.stars.desc(), GitHubRepo.id)
    ).all()
    selected: list[GitHubRepo] = []
    attempted = 0
    failed = False
    for candidate in candidates:
        if not _matches_topics(candidate, topics):
            continue
        attempted += 1
        if ensure_repo_intel(db, candidate, client=client):
            if (candidate.testing_value_score or 0) >= settings.testing_relevance_threshold:
                selected.append(candidate)
        else:
            failed = True
        if len(selected) >= min(top_n, 5) or attempted >= 10:
            break
    if not selected:
        return None, failed
    day = now.astimezone(BUSINESS_TZ).date()
    report = GitHubReport(
        report_key=f"daily:{day}",
        report_type="daily",
        title=f"GitHub 项目日报 {day}",
        period_start=now - timedelta(days=1),
        period_end=now,
        status="ready",
        generated_at=now,
    )
    db.add(report)
    for rank, candidate in enumerate(selected, 1):
        report.items.append(
            GitHubReportItem(
                repo_id=candidate.id,
                rank=rank,
                momentum_score=candidate.momentum_score,
                momentum_tier=candidate.momentum_tier,
                highlight=candidate.intel_summary,
            )
        )
    report.body_markdown = render_daily_markdown(report.title, selected)
    return report, False


def run_github_exploration(
    db: Session,
    *,
    now: datetime | None = None,
    force: bool = False,
    discover: bool = True,
    languages: list[str] | None = None,
    topics: list[str] | None = None,
    client: httpx.Client | None = None,
    top_n: int = 5,
) -> GitHubExplorationState:
    started = now or datetime.now(UTC)
    token = claim_exploration(db, now=started, force=force)
    if token is None:
        return get_exploration_state(db)
    count = 0
    try:
        if discover:
            count = discover_candidates(
                db,
                languages or settings.github_discovery_languages_list,
                topics=get_topics(db) if topics is None else topics,
                client=client,
                now=started,
            )
            # Keep search progress if analysis needs a later retry.
            with db.no_autoflush:
                lease = db.execute(
                    update(GitHubExplorationState)
                    .where(
                        GitHubExplorationState.id == 1,
                        GitHubExplorationState.lease_token == token,
                    )
                    .values(lease_token=token)
                    .execution_options(synchronize_session=False)
                )
            if not lease.rowcount:
                raise ExplorationLeaseLost("Search lease was replaced")
            db.commit()
        report, failed = _build_daily_report(db, now=started, top_n=top_n, client=client)
        finish_exploration(
            db,
            token,
            now=now or datetime.now(UTC),
            report=report,
            discovered=count,
            error="项目分析暂未完成，平台将在稍后自动重试。" if failed else None,
        )
    except ExplorationLeaseLost:
        db.rollback()
    except Exception as exc:
        db.rollback()
        retry_seconds = 3600
        if isinstance(exc, httpx.HTTPStatusError):
            from app.github_client import retry_after_seconds

            retry_seconds = max(retry_seconds, retry_after_seconds(exc, now=started) or 0)
        try:
            finish_exploration(
                db,
                token,
                now=now or datetime.now(UTC),
                report=None,
                discovered=count,
                error="自动探索暂未完成，平台将在稍后自动重试。",
                retry_seconds=retry_seconds,
            )
        except ExplorationLeaseLost:
            db.rollback()
    return get_exploration_state(db)


def generate_daily_report(
    db: Session,
    *,
    now: datetime | None = None,
    top_n: int = 5,
    client: httpx.Client | None = None,
) -> GitHubReport | None:
    now = now or datetime.now(UTC)
    existing = _report_for_day(db, now)
    if existing:
        return existing
    state = run_github_exploration(
        db,
        now=now,
        force=True,
        discover=False,
        top_n=top_n,
        client=client,
    )
    return db.get(GitHubReport, state.report_id) if state.report_id else None
