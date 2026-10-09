"""Bounded search with durable pagination and historical publication exclusion."""

from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.github_client import search_repositories
from app.github_discovery import (
    build_discovery_queries,
    build_topic_queries,
    estimate_star_deltas,
    repo_from_search_item,
    score_candidate,
)
from app.models import GitHubExplorationState, GitHubRepo, GitHubReportItem

SEARCH_BUDGET = 8


def discover_new_candidates(
    db: Session,
    languages: list[str],
    *,
    topics: list[str] | None = None,
    stars_min: int = 20,
    per_language: int = 30,
    client: httpx.Client | None = None,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(UTC)
    bases = build_topic_queries(topics or [], stars_min)
    if not bases:
        bases = build_discovery_queries(languages, stars_min)
    queries = []
    for base in dict.fromkeys(bases):
        for kind, qualifier, days, sort in [
            ("new", "created", 90, "stars"),
            ("active", "pushed", 30, "updated"),
        ]:
            since = (now - timedelta(days=days)).date().isoformat()
            queries.append(
                (f"{base}|{kind}", f"{base} {qualifier}:>={since} archived:false fork:false", sort)
            )
    if not queries:
        return 0
    state = db.get(GitHubExplorationState, 1)
    if state is None:
        state = GitHubExplorationState(id=1, search_cursors={})
        db.add(state)
        db.flush()
    saved = state.search_cursors or {}
    pages = {key: max(1, int(saved.get("pages", {}).get(key, 1))) for key, _, _ in queries}
    position = int(saved.get("position", 0)) % len(queries)
    known = list(db.scalars(select(GitHubRepo)))
    by_id = {repo.github_id: repo for repo in known if repo.github_id is not None}
    by_name = {repo.full_name.casefold(): repo for repo in known}
    historical = set(db.scalars(select(GitHubReportItem.repo_id)))
    exhausted: set[str] = set()
    seen: set[tuple] = set()
    count = 0
    per_page = max(1, min(per_language, 100))
    requests = 0
    while requests < SEARCH_BUDGET and len(exhausted) < len(queries):
        key, query, sort = queries[position]
        position = (position + 1) % len(queries)
        if key in exhausted:
            continue
        page = pages[key]
        payload = search_repositories(query, client=client, page=page, per_page=per_page, sort=sort)
        requests += 1
        if payload.get("incomplete_results"):
            raise RuntimeError("GitHub returned incomplete search results")
        items = payload.get("items") or []
        for item in items:
            found = repo_from_search_item(item)
            identity = (
                ("id", found.github_id)
                if found.github_id is not None
                else ("name", found.full_name.casefold())
            )
            if identity in seen:
                continue
            seen.add(identity)
            existing = by_id.get(found.github_id) or by_name.get(found.full_name.casefold())
            if existing is not None and existing.github_id is None and found.github_id is not None:
                existing.github_id = found.github_id
                by_id[found.github_id] = existing
            if existing is not None and (existing.id in historical or existing.status == "ignored"):
                continue
            if found.archived or found.fork:
                if existing is not None:
                    existing.archived = True
                continue
            if existing is None:
                existing = GitHubRepo(
                    full_name=found.full_name,
                    github_id=found.github_id,
                    status="discovered",
                    first_seen_at=now,
                )
                db.add(existing)
                count += 1
            existing.full_name = found.full_name
            existing.html_url = found.html_url
            existing.description = found.description
            existing.primary_language = found.language
            existing.topics = found.topics
            existing.homepage = found.homepage
            existing.license_name = found.license_name
            existing.archived = found.archived
            existing.stars = found.stars
            existing.forks = found.forks
            existing.open_issues = found.open_issues
            existing.watchers = found.watchers
            existing.repo_created_at = found.created_at
            existing.repo_pushed_at = found.pushed_at
            existing.last_seen_at = now
            d24, d7 = estimate_star_deltas(found.stars, found.created_at or now, now)
            momentum = score_candidate(found.stars, d24, d7, found.pushed_at, now)
            existing.momentum_score = momentum.score
            existing.momentum_tier = momentum.tier
            by_name[found.full_name.casefold()] = existing
            if found.github_id is not None:
                by_id[found.github_id] = existing
        limit = min(int(payload.get("total_count", 1000)), 1000)
        if len(items) < per_page or (page + 1) * per_page > 1000 or page * per_page >= limit:
            pages[key] = 1
            exhausted.add(key)
        else:
            pages[key] = page + 1
    state.search_cursors = {"pages": pages, "position": position}
    db.flush()
    return count
