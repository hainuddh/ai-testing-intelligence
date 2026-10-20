"""Bounded discovery and isolated evidence trials, with explicit human promotion."""

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.analyzer import analyze_content
from app.cache import delete_prefix_sync
from app.config import settings
from app.fetcher import FetchedItem, download_feed
from app.models import (
    ContentItem,
    Source,
    SourceCandidate,
    SourceDiscoveryPage,
    SourceEndpoint,
    SourceExplorationPreference,
)
from app.source_catalog import CATALOG, CatalogSource
from app.source_management import (
    ensure_feed_available,
    lock_source_configuration,
    normalize_feed_url,
)

SCOPE_NOTE = (
    "从维护目录、已分析内容与试运行样例的真实链接扩展，最多两层；支持订阅、网页列表和独立文章，"
    "不使用全网搜索。文章作为网站发现线索；确认后收录网站并探测RSS/Atom或网页列表。"
)
DAY = timedelta(days=1)
ACTIVE = ("trial", "ready")
MAX_SAMPLES = 20
SAMPLES_PER_CHECK = 5
ALIASES = (
    ("testing", "test", "quality", "qa", "测试", "软件质量", "自动化测试", "接口自动化", "ai测试"),
    ("agents", "agent", "智能体", "agent评测", "ai辅助测试"),
    ("llm", "ai", "人工智能", "大模型", "foundation-models"),
    ("evaluation", "eval", "评测", "模型评估"),
    ("automation", "自动化", "browser", "浏览器"),
    ("security", "安全", "ai-safety", "安全测试"),
)


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC)


def clean_terms(values: list[str]) -> list[str]:
    ordered: dict[str, str] = {}
    for raw in values:
        value = raw.strip()
        if value:
            ordered.setdefault(value.casefold(), value)
    return list(ordered.values())


def matches(text: str, term: str) -> bool:
    term, text = term.casefold(), text.casefold()
    terms = next((group for group in ALIASES if term in group), (term,))
    return any(
        (
            re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", text)
            if word.isascii()
            else word in text
        )
        for word in terms
    )


def matched_topics(text: str, topics: list[str]) -> list[str]:
    return [term for term in topics if matches(text, term)]


def preferences(db: Session) -> SourceExplorationPreference:
    result = db.get(SourceExplorationPreference, 1)
    if result is None:
        result = SourceExplorationPreference(id=1)
        db.add(result)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        result = db.get(SourceExplorationPreference, 1)
    return result


def fingerprint(pref) -> tuple:
    return (
        pref.enabled,
        tuple(pref.topics),
        tuple(pref.languages),
        tuple(pref.excluded_terms),
        pref.max_trial_sources,
    )


def public_web_url(value: str) -> bool:
    parts = urlsplit(value)
    return parts.scheme in {"http", "https"} and bool(parts.hostname) and not parts.username


def discovery_origins(db: Session, pref) -> list[tuple[CatalogSource, str, str]]:
    origins = [(entry, "catalog", entry.homepage_url) for entry in CATALOG]
    rows = db.execute(
        select(ContentItem.url, ContentItem.related_links)
        .join(Source)
        .where(
            Source.status.not_in(["paused", "archived"]),
            ContentItem.analysis_status == "analyzed",
        )
        .order_by(ContentItem.analyzed_at.desc())
        .limit(100)
    ).all()
    for article_url, links in rows:
        for link in (links or [])[:5]:
            if not isinstance(link, dict):
                continue
            url = link.get("url", "")
            title = link.get("title", "")
            if not isinstance(url, str) or not isinstance(title, str) or not public_web_url(url):
                continue
            directions = matched_topics(title + " " + url, pref.topics)
            if directions:
                parts = urlsplit(url)
                home = urlunsplit((parts.scheme, parts.netloc, "/", "", ""))
                origins.append(
                    (
                        CatalogSource(parts.hostname, home, tuple(directions), ""),
                        "article_link",
                        article_url,
                    )
                )
    return [
        (entry, kind, evidence)
        for entry, kind, evidence in origins
        if (
            (not pref.languages or not entry.language or entry.language in pref.languages)
            and matched_topics(" ".join((entry.name, *entry.topics)), pref.topics)
            and not any(
                matches(" ".join((entry.name, entry.homepage_url, *entry.topics)), word)
                for word in pref.excluded_terms
            )
        )
    ]


def sample_evidence(
    candidate, items: list[FetchedItem], pref, *, analysis_budget=SAMPLES_PER_CHECK
) -> list[dict]:
    samples = [dict(sample) for sample in candidate.samples]
    seen = set(candidate.seen_urls)
    for item in items[:SAMPLES_PER_CHECK]:
        if len(samples) >= MAX_SAMPLES or not public_web_url(item.url):
            continue
        key = normalize_feed_url(item.url)
        if key in seen:
            continue
        if any(
            matches(item.title + " " + (item.summary or ""), word) for word in pref.excluded_terms
        ):
            continue
        seen.add(key)
        samples.append(
            {
                "title": item.title,
                "url": item.url,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "summary": (item.summary or "")[:4000],
                "body": (item.body or "")[:50000],
                "analysis_status": "pending",
                "testing_relevance_score": None,
                "testing_value_score": None,
                "analysis_summary": None,
                "matched_topics": matched_topics(
                    item.title + " " + (item.summary or ""), pref.topics
                ),
            }
        )
    budget = analysis_budget
    if settings.analysis_api_base_url and settings.analysis_model:
        for sample in samples:
            if sample["analysis_status"] == "analyzed" or budget == 0:
                continue
            budget -= 1
            # Analyze only the actual feed evidence. Do not create formal rows or fetch full pages.
            source = Source(
                name=candidate.name,
                source_type="rss",
                topics=candidate.topics,
                languages=[candidate.language] if candidate.language else [],
                trust_level=3,
            )
            item = ContentItem(
                source=source,
                title=sample["title"],
                url=sample["url"],
                summary=sample["summary"],
                body=sample.get("body") or sample["summary"] or sample["title"],
            )
            try:
                result = analyze_content(item)
            except Exception:
                sample["analysis_status"] = "failed"
            else:
                sample.update(
                    analysis_status="analyzed",
                    analysis_summary=result.analysis_summary,
                    testing_relevance_score=result.testing_relevance_score,
                    testing_value_score=result.testing_value_score,
                    is_testing_relevant=result.is_testing_relevant,
                )
    return samples


def current_lease(db: Session, token: str, expected: tuple):
    pref = db.get(SourceExplorationPreference, 1, populate_existing=True, with_for_update=True)
    return pref if pref and pref.lease_token == token and fingerprint(pref) == expected else None


def snapshot(candidate) -> SimpleNamespace:
    return SimpleNamespace(
        **{
            field: getattr(candidate, field)
            for field in (
                "id",
                "name",
                "homepage_url",
                "feed_url",
                "topics",
                "language",
                "samples",
                "seen_urls",
            )
        }
    )


def run_source_exploration(db: Session, *, now: datetime | None = None) -> None:
    now = utc(now or datetime.now(UTC))
    pref = preferences(db)
    if not pref.enabled or not pref.topics:
        db.commit()
        return
    token = str(uuid4())
    claimed = db.execute(
        update(SourceExplorationPreference)
        .where(
            SourceExplorationPreference.id == 1,
            or_(
                SourceExplorationPreference.next_run_at.is_(None),
                SourceExplorationPreference.next_run_at <= now,
            ),
            or_(
                SourceExplorationPreference.lease_until.is_(None),
                SourceExplorationPreference.lease_until <= now,
            ),
        )
        .values(lease_token=token, lease_until=now + timedelta(hours=1), last_started_at=now)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if claimed.rowcount != 1:
        return
    pref = db.get(SourceExplorationPreference, 1, populate_existing=True)
    expected = fingerprint(pref)
    policy = SimpleNamespace(
        enabled=pref.enabled,
        topics=list(pref.topics),
        languages=list(pref.languages),
        excluded_terms=list(pref.excluded_terms),
        max_trial_sources=pref.max_trial_sources,
        cursor=pref.discovery_cursor,
    )
    error = None
    next_cursor = policy.cursor
    try:
        trials = list(
            db.scalars(
                select(SourceCandidate)
                .where(
                    SourceCandidate.status.in_(ACTIVE),
                    SourceCandidate.endpoint_type == "rss",
                    or_(
                        SourceCandidate.sample_count < MAX_SAMPLES,
                        SourceCandidate.analysis_count < SourceCandidate.sample_count,
                    ),
                    or_(
                        SourceCandidate.next_check_at.is_(None),
                        SourceCandidate.next_check_at <= now,
                    ),
                )
                .order_by(SourceCandidate.id)
                .limit(policy.max_trial_sources)
            )
        )
        work = [snapshot(candidate) for candidate in trials]
        known_homepages = {
            normalize_feed_url(value) for value in db.scalars(select(SourceCandidate.homepage_url))
        }
        installed = {normalize_feed_url(value) for value in db.scalars(select(SourceEndpoint.url))}
        installed_homepages = {
            normalize_feed_url(value)
            for value in db.scalars(
                select(Source.homepage_url).where(Source.homepage_url.is_not(None))
            )
        }
        count = db.scalar(
            select(func.count())
            .select_from(SourceCandidate)
            .where(SourceCandidate.status.in_(ACTIVE))
            .where(SourceCandidate.endpoint_type != "article")
        )
        origins = discovery_origins(db, policy)
        if origins:
            start = policy.cursor % len(origins)
            origins = origins[start:] + origins[:start]
        db.commit()
        attempts = 0
        for entry, kind, evidence in origins:
            if count >= policy.max_trial_sources or attempts >= 6:
                break
            home = normalize_feed_url(entry.homepage_url)
            if home in known_homepages or home in installed_homepages:
                continue
            attempts += 1
            next_cursor = (policy.cursor + origins.index((entry, kind, evidence)) + 1) % len(
                origins
            )
            known_homepages.add(home)
            try:
                feed_url, items = download_feed(
                    entry.feed_url or entry.homepage_url, SAMPLES_PER_CHECK, entry.homepage_url
                )
            except Exception:
                error = "部分候选网站暂未验证通过，平台将在后续探索中重试。"
                continue
            feed_url = normalize_feed_url(feed_url)
            if feed_url in installed:
                continue
            current = current_lease(db, token, expected)
            if current is None:
                return
            existing = db.scalar(
                select(SourceCandidate.id).where(SourceCandidate.feed_url == feed_url)
            )
            if existing is not None:
                db.commit()
                continue
            candidate = SourceCandidate(
                name=entry.name[:200],
                homepage_url=entry.homepage_url,
                feed_url=feed_url,
                topics=list(entry.topics),
                language=entry.language,
                discovery_kind=kind,
                evidence_url=evidence,
                reason="匹配关注方向："
                + "、".join(matched_topics(" ".join((entry.name, *entry.topics)), policy.topics)),
            )
            db.add(candidate)
            db.flush()
            trial = snapshot(candidate)
            db.commit()
            work.append((trial, items))
            count += 1
            installed.add(feed_url)
        for entry in work:
            trial, items = entry if isinstance(entry, tuple) else (entry, None)
            try:
                if items is None:
                    # End every read transaction before calling a network service.
                    db.commit()
                    _url, items = download_feed(
                        trial.feed_url, SAMPLES_PER_CHECK, trial.homepage_url
                    )
                evidence = sample_evidence(trial, items, policy)
            except Exception:
                if current_lease(db, token, expected) is None:
                    return
                candidate = db.get(SourceCandidate, trial.id, populate_existing=True)
                if candidate and candidate.status in ACTIVE:
                    candidate.consecutive_failures += 1
                    candidate.check_count += 1
                    candidate.last_error = "暂时无法读取订阅内容，平台将退避重试；历史样例仍保留。"
                    candidate.last_checked_at = now
                    candidate.next_check_at = now + timedelta(
                        hours=min(24, 2 ** (candidate.consecutive_failures - 1))
                    )
                db.commit()
                continue
            if current_lease(db, token, expected) is None:
                return
            candidate = db.scalar(
                select(SourceCandidate)
                .where(SourceCandidate.id == trial.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if candidate is None or candidate.status not in ACTIVE:
                db.commit()
                continue
            candidate.samples = evidence
            candidate.seen_urls = [normalize_feed_url(sample["url"]) for sample in evidence]
            candidate.sample_count = len(evidence)
            analyzed = [sample for sample in evidence if sample["analysis_status"] == "analyzed"]
            candidate.analysis_count = len(analyzed)
            candidate.relevant_count = sum(
                bool(sample.get("is_testing_relevant"))
                and (sample["testing_relevance_score"] >= settings.testing_relevance_threshold)
                for sample in analyzed
            )
            candidate.status = "ready" if len(analyzed) >= 5 else "trial"
            candidate.last_checked_at = now
            candidate.next_check_at = now + DAY
            candidate.last_error = (
                "部分样例分析失败，后续检查时重试。"
                if any(s["analysis_status"] == "failed" for s in evidence)
                else None
            )
            candidate.consecutive_failures = 0
            candidate.check_count += 1
            candidate.success_count += 1
            db.commit()
        from app.web_discovery import run_web_exploration

        run_web_exploration(
            db,
            policy,
            now,
            origins,
            lambda: current_lease(db, token, expected) is not None,
        )
    except Exception:
        db.rollback()
        error = "本轮探索未完成，平台将稍后重试。"
    finally:
        db.rollback()
        current = current_lease(db, token, expected)
        if current:
            retry = db.scalar(
                select(func.min(SourceCandidate.next_check_at)).where(
                    SourceCandidate.status.in_(ACTIVE), SourceCandidate.next_check_at > now
                )
            )
            web_retry = db.scalar(
                select(func.min(SourceDiscoveryPage.next_check_at)).where(
                    SourceDiscoveryPage.status != "ignored",
                    SourceDiscoveryPage.next_check_at > now,
                )
            )
            if web_retry is not None:
                retry = min(utc(retry), utc(web_retry)) if retry else web_retry
            current.last_completed_at, current.last_error = now, error
            current.discovery_cursor = next_cursor
            current.next_run_at = min(now + DAY, utc(retry)) if retry else now + DAY
        db.execute(
            update(SourceExplorationPreference)
            .where(
                SourceExplorationPreference.id == 1,
                SourceExplorationPreference.lease_token == token,
            )
            .values(lease_token=None, lease_until=None)
        )
        db.commit()


def promote_candidate(db: Session, candidate_id: int, user_id: int) -> dict:
    candidate = db.get(SourceCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="试运行信源不存在，请刷新后重试。")
    if candidate.status == "promoted":
        return {"candidate": candidate, "source_id": candidate.promoted_source_id}
    if candidate.status not in ACTIVE or candidate.sample_count == 0:
        raise HTTPException(
            status_code=409, detail="请先恢复试运行，并查看实际文章样例后再正式启用。"
        )
    return promote_website(db, candidate, user_id)


def promote_website(db: Session, candidate: SourceCandidate, user_id: int) -> dict:
    """Admit a website from observed evidence; sample articles stay isolated."""
    from app.fetcher import normalized_link_url
    from app.web_discovery import read_listing

    candidate_id = candidate.id
    if not normalized_link_url(candidate.homepage_url):
        raise HTTPException(status_code=422, detail="网站主页不是有效的公开网址，无法收录。")
    homepage = normalize_feed_url(candidate.homepage_url)
    evidence_page, topics = candidate.feed_url, list(candidate.topics)
    known_rss = candidate.endpoint_type == "rss"
    db.commit()
    verified, endpoint_type = None, None
    try:
        feed, _items = download_feed(
            evidence_page if known_rss else homepage,
            3,
            homepage if known_rss else evidence_page,
        )
        # Validate with the public-URL guard without rewriting signed/feed query parameters.
        verified = normalize_feed_url(feed) if normalized_link_url(feed) else None
        if verified:
            endpoint_type = "rss"
    except Exception as exc:
        if known_rss:
            raise HTTPException(status_code=422, detail="暂时无法验证此订阅，请稍后重试。") from exc
        # A website can still be admitted when RSS discovery is unavailable.
        pass
    if known_rss and verified is None:
        raise HTTPException(status_code=422, detail="订阅地址不是有效的公开网址，无法启用。")
    if verified is None:
        try:
            if read_listing(homepage, topics, 3):
                verified, endpoint_type = homepage, "web_listing"
        except Exception:
            pass

    lock_source_configuration(db)
    candidate = db.scalar(
        select(SourceCandidate)
        .where(SourceCandidate.id == candidate_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="试运行信源已不存在，请刷新后重试。")
    if candidate.status == "promoted":
        return {"candidate": candidate, "source_id": candidate.promoted_source_id}
    if candidate.status not in ACTIVE or not candidate.samples:
        raise HTTPException(status_code=409, detail="候选状态已改变，请刷新后重试。")
    normalized_verified = normalize_feed_url(verified) if verified else None
    existing_endpoint = next(
        (
            endpoint
            for endpoint in db.scalars(select(SourceEndpoint))
            if normalized_verified and normalize_feed_url(endpoint.url) == normalized_verified
        ),
        None,
    )
    source = (
        db.get(Source, existing_endpoint.source_id)
        if existing_endpoint
        else next(
            (
                site
                for site in db.scalars(
                    select(Source).where(Source.homepage_url.is_not(None)).order_by(Source.id)
                )
                if normalize_feed_url(site.homepage_url) == normalize_feed_url(homepage)
            ),
            None,
        )
    )
    if source is None:
        name = (
            (urlsplit(homepage).hostname or homepage)
            if candidate.endpoint_type == "article"
            else candidate.name
        )
        name = name[:200]
        base, ordinal = name, 1
        while db.scalar(select(Source.id).where(Source.name == name)):
            suffix = f" · {candidate.id}" if ordinal == 1 else f" · {candidate.id}-{ordinal}"
            name = base[: 200 - len(suffix)] + suffix
            ordinal += 1
        note = (
            "已验证RSS/Atom订阅。"
            if endpoint_type == "rss"
            else "暂未发现已验证RSS/Atom，使用已验证网页列表持续采集。"
            if endpoint_type
            else "RSS/Atom与网页列表暂未验证通过，网站已收录，待配置采集端点。"
        )
        source = Source(
            name=name,
            source_type="website",
            homepage_url=homepage,
            topics=candidate.topics,
            languages=[candidate.language] if candidate.language else [],
            trust_level=3,
            status="draft",
            created_by=user_id,
            description=candidate.reason + "；" + note,
        )
        db.add(source)
        db.flush()
    if verified and existing_endpoint is None:
        ensure_feed_available(db, verified)
        db.add(
            SourceEndpoint(
                source_id=source.id,
                name="已验证RSS/Atom订阅" if endpoint_type == "rss" else "已验证网页列表",
                endpoint_type=endpoint_type,
                url=normalized_verified,
                max_items_per_run=50,
            )
        )
        if source.status == "draft":
            source.status = "active"
    candidate.status, candidate.promoted_source_id = "promoted", source.id
    candidate.last_error = (
        None if verified else "网站已收录，暂未验证到RSS/Atom或网页列表，请在信源管理中配置端点。"
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="信源配置刚刚发生变化，请刷新后重试。") from exc
    db.refresh(candidate)
    for prefix in ("sources:list", "sources:endpoints"):
        delete_prefix_sync(prefix)
    return {"candidate": candidate, "source_id": source.id}
