"""Discover real public links without search, with durable progress and bounded reads."""

import time
from collections import Counter
from datetime import timedelta
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import httpx
from sqlalchemy import case, func, or_, select

from app.fetcher import (
    RelatedLinkParser,
    download,
    is_feed_document,
    local_name,
    normalized_link_url,
    parse_web,
    request_guard,
)
from app.models import ContentItem, Source, SourceCandidate, SourceDiscoveryPage

REQUEST_BUDGET = 24
PER_HOST_BUDGET = 8
MAX_DEPTH = 2
MAX_FRONTIER = 2000
ARTICLE_CAP = 10
USER_AGENT = "ATI-Fetcher/0.1"
MANUAL_ONLY_HOSTS = ("weibo.com", "weibo.cn", "mp.weixin.qq.com")


def public_url(url):
    normalized = normalized_link_url(url)
    host = (urlsplit(normalized).hostname or "") if normalized else ""
    return (
        normalized
        if normalized and not any(host == h or host.endswith("." + h) for h in MANUAL_ONLY_HOSTS)
        else None
    )


class BudgetExceeded(ValueError):
    pass


class PageMetadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.language = ""
        self.article = False
        self.article_blocks = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "html":
            self.language = (values.get("lang") or "").split("-")[0].lower()
        if tag == "article" or (
            tag == "meta" and values.get("property") == "article:published_time"
        ):
            self.article = True
        if tag == "article":
            self.article_blocks += 1


def origin(url):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "/", "", ""))


def page_links(data, url, topics):
    """Prefer content links; keep same-site navigation only when it leads to content."""
    from app.source_exploration import matched_topics

    parser = RelatedLinkParser(url)
    parser.feed(data.decode("utf-8", errors="replace"))
    selected = []
    seen = set()
    for title, raw, in_content in parser.links:
        link = public_url(raw)
        if not link or link == normalized_link_url(url) or link in seen:
            continue
        path = urlsplit(link).path.lower()
        if any(
            part in path.split("/")
            for part in (
                "login",
                "signin",
                "signup",
                "account",
                "privacy",
                "terms",
                "search",
                "logout",
            )
        ) or path.endswith((".png", ".jpg", ".css", ".js", ".zip", ".pdf", ".mp4")):
            continue
        relevant = matched_topics(title + " " + link, topics)
        if not relevant and not (in_content and origin(link) == origin(url)):
            continue
        seen.add(link)
        selected.append({"title": title[:500], "url": link})
    return selected[:10]


class Reader:
    def __init__(self):
        self.used = 0
        self.hosts = Counter()
        self.started = time.monotonic()
        self.robots = {}
        self.sitemaps = {}

    def reserve(self, url):
        host = urlsplit(url).netloc
        if (
            self.used >= REQUEST_BUDGET
            or self.hosts[host] >= PER_HOST_BUDGET
            or time.monotonic() - self.started > 90
        ):
            raise BudgetExceeded("本轮网页访问预算已用完，后续轮次继续。")
        self.used += 1
        self.hosts[host] += 1

    def get(self, url):
        if not public_url(url):
            raise ValueError("仅访问公开网页")
        self.reserve(url)
        first = True

        def guard(redirect_url):
            nonlocal first
            if origin(redirect_url) != origin(url):
                raise ValueError("跨站跳转留待独立发现，不自动跟随内容。")
            if first:
                first = False
            else:
                self.reserve(redirect_url)

        token = request_guard.set(guard)
        try:
            final, data, content_type = download(url)
            if origin(final) != origin(url):
                raise ValueError("跨站跳转留待独立发现，不自动跟随内容。")
            return final, data, content_type
        finally:
            request_guard.reset(token)

    def allowed(self, url):
        home = origin(url)
        if home not in self.robots:
            parser = RobotFileParser()
            try:
                _final, data, _kind = self.get(urljoin(home, "robots.txt"))
                parser.parse(data.decode("utf-8", errors="replace").splitlines())
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {404, 410}:
                    raise
                parser.parse([])
            self.robots[home] = parser
            self.sitemaps[home] = [
                value
                for value in (parser.site_maps() or [])[:2]
                if normalized_link_url(value) and origin(value) == home
            ]
        return self.robots[home].can_fetch(USER_AGENT, url)

    def html(self, url):
        if not self.allowed(url):
            raise ValueError("网站robots规则不允许自动读取该页面。")
        final, data, kind = self.get(url)
        if "html" not in kind.lower() or is_feed_document(data):
            raise ValueError("当前页面不是可分析的HTML内容。")
        text = data[:8192].decode("utf-8", errors="replace").casefold()
        if any(
            marker in text
            for marker in (
                "just a moment...",
                "cf-chl-",
                "attention required! | cloudflare",
            )
        ):
            raise ValueError("网站返回验证页，未取得正文。")
        return final, data

    def sitemap_links(self, url, topics):
        from app.source_exploration import matched_topics

        result = []
        for sitemap in self.sitemaps.get(origin(url), []):
            if not self.allowed(sitemap):
                continue
            _final, data, _kind = self.get(sitemap)
            root = ElementTree.fromstring(data)
            # One advertised urlset only; sitemap indexes never start an unbounded traversal.
            if local_name(root.tag) != "urlset":
                continue
            for node in root.iter():
                value = (node.text or "").strip()
                if (
                    local_name(node.tag) == "loc"
                    and normalized_link_url(value)
                    and origin(value) == origin(url)
                    and matched_topics(value, topics)
                ):
                    result.append({"title": value, "url": value})
                    if len(result) >= 10:
                        return result
        return result


def read_listing(url, topics, limit=5, *, reader=None, seen_urls=None):
    """Used by both trial and formal collection; never returns the listing as an article."""
    reader = reader or Reader()
    final, data = reader.html(url)
    links = [
        link for link in page_links(data, final, topics) if origin(link["url"]) == origin(final)
    ]
    if not links:
        links = reader.sitemap_links(final, topics)
    seen = {normalized_link_url(value) for value in (seen_urls or [])}
    links = [link for link in links if link["url"] not in seen]
    items = []
    failures = 0
    for link in links[: min(limit, 5)]:
        try:
            page_url, page = reader.html(link["url"])
            item = parse_web(page, page_url)[0]
            if item.body:
                items.append(item)
        except BudgetExceeded:
            break
        except Exception:
            failures += 1
    if not items and failures:
        raise ValueError("网页列表可达，但文章暂时无法读取。")
    return items


def seed_frontier(db, policy, origins):
    from app.source_exploration import matched_topics

    seeds = [
        (entry.homepage_url, entry.name, list(entry.topics), evidence, 0)
        for entry, _kind, evidence in origins
    ]
    rows = db.execute(
        select(ContentItem.url, ContentItem.title, ContentItem.related_links)
        .join(Source)
        .where(
            Source.status.not_in(["paused", "archived"]), ContentItem.analysis_status == "analyzed"
        )
        .order_by(ContentItem.analyzed_at.desc(), ContentItem.id.desc())
        .limit(100)
    ).all()
    for article, title, links in rows:
        # The original article is a traversal anchor; it is already deduplicated on publication.
        seeds.append((article, title, matched_topics(title, policy.topics), article, 0))
        for link in (links or [])[:5]:
            if isinstance(link, dict) and isinstance(link.get("url"), str):
                title = link.get("title", "")
                if isinstance(title, str):
                    seeds.append(
                        (
                            link["url"],
                            title,
                            matched_topics(title + " " + link["url"], policy.topics),
                            article,
                            0,
                        )
                    )
    # Real trial samples can reveal useful sites even before a subscription is enabled.
    for candidate in db.scalars(
        select(SourceCandidate).where(SourceCandidate.status.in_(["trial", "ready"])).limit(30)
    ):
        for sample in candidate.samples[:5]:
            seeds.append(
                (sample["url"], sample.get("title", ""), candidate.topics, candidate.feed_url, 1)
            )
    for url, title, topics, evidence, depth in seeds[:200]:
        enqueue(db, url, title, topics, evidence, depth, policy)


def enqueue(db, url, title, topics, evidence, depth, policy):
    from app.source_exploration import matched_topics, matches

    url = public_url(url)
    if (
        not url
        or depth > MAX_DEPTH
        or not matched_topics(" ".join(topics) + " " + title + " " + url, policy.topics)
        or any(matches(title + " " + url, word) for word in policy.excluded_terms)
    ):
        return
    from app.content_deduplication import content_url_hash

    digest = content_url_hash(url)
    if db.scalar(select(SourceDiscoveryPage.id).where(SourceDiscoveryPage.url_hash == digest)):
        return
    if db.scalar(select(func.count()).select_from(SourceDiscoveryPage)) >= MAX_FRONTIER:
        # Retire an already visited URL, never a waiting task or a user's candidate decision.
        retired = db.scalar(
            select(SourceDiscoveryPage)
            .where(
                SourceDiscoveryPage.status == "visited",
                ~SourceDiscoveryPage.url.in_(select(SourceCandidate.feed_url)),
                SourceDiscoveryPage.id != getattr(policy, "current_page_id", -1),
            )
            .order_by(SourceDiscoveryPage.id)
            .limit(1)
        )
        if retired is None:
            return
        db.delete(retired)
        db.flush()
    db.add(
        SourceDiscoveryPage(
            url=url,
            url_hash=digest,
            title=title[:500],
            topics=topics,
            evidence_url=evidence,
            depth=depth,
        )
    )
    db.flush()


def candidate_summary(candidate):
    analyzed = candidate.analysis_count
    ratio = round(candidate.relevant_count / analyzed * 100) if analyzed else None
    values = [
        s["testing_value_score"]
        for s in candidate.samples
        if s.get("analysis_status") == "analyzed" and s.get("testing_value_score") is not None
    ]
    average = round(sum(values) / len(values)) if values else None
    if candidate.endpoint_type == "article":
        recommendation = "单篇内容，确认后收录，不创建持续采集任务。"
    elif analyzed < 5:
        recommendation = "继续观察：分析样本不足，低频更新不代表低质量。"
    elif ratio >= 60 and average is not None and average >= 60:
        recommendation = "值得持续跟踪：已有多篇相关且有测试价值的样例，仍需确认启用。"
    else:
        recommendation = "谨慎启用：请结合样例判断相关性和实际测试价值。"
    return {
        "relevant_ratio": ratio,
        "average_value": average,
        "check_count": candidate.check_count,
        "success_count": candidate.success_count,
        "recommendation": recommendation,
    }


def run_web_exploration(db, policy, now, origins, lease_valid):
    from app.source_exploration import ACTIVE, matched_topics, matches

    if not lease_valid():
        return
    seed_frontier(db, policy, origins)
    db.commit()
    policy.web_analysis_remaining = 10
    # Retry analysis from saved evidence independently of the next network visit.
    pending = list(
        db.scalars(
            select(SourceCandidate)
            .where(
                SourceCandidate.endpoint_type != "rss",
                SourceCandidate.status.in_(ACTIVE),
                SourceCandidate.analysis_count < SourceCandidate.sample_count,
                or_(SourceCandidate.next_check_at.is_(None), SourceCandidate.next_check_at <= now),
            )
            .order_by(SourceCandidate.next_check_at, SourceCandidate.id)
            .limit(ARTICLE_CAP + policy.max_trial_sources)
        )
    )
    pending_work = [trial_snapshot(candidate) for candidate in pending]
    db.commit()
    for trial in pending_work:
        if policy.web_analysis_remaining <= 0:
            break
        if not save_samples(db, trial, [], policy, now, lease_valid):
            return
    reader = Reader()
    visited = set()
    while reader.used < REQUEST_BUDGET and time.monotonic() - reader.started <= 90:
        if not lease_valid():
            return
        page = db.scalar(
            select(SourceDiscoveryPage)
            .where(
                SourceDiscoveryPage.status != "ignored",
                or_(
                    SourceDiscoveryPage.next_check_at.is_(None),
                    SourceDiscoveryPage.next_check_at <= now,
                ),
                SourceDiscoveryPage.id.not_in(visited),
            )
            .order_by(
                case((SourceDiscoveryPage.status == "pending", 0), else_=1),
                SourceDiscoveryPage.next_check_at,
                SourceDiscoveryPage.depth,
                SourceDiscoveryPage.id,
            )
            .limit(1)
        )
        if page is None:
            db.commit()
            break
        visited.add(page.id)
        policy.current_page_id = page.id
        identity, url, evidence, depth = page.id, page.url, page.evidence_url, page.depth
        candidate = db.scalar(select(SourceCandidate).where(SourceCandidate.feed_url == url))
        closed_home = db.scalar(
            select(SourceCandidate.id).where(
                SourceCandidate.homepage_url == origin(url),
                SourceCandidate.endpoint_type != "article",
                SourceCandidate.status.in_(["paused", "dismissed"]),
            )
        )
        stopped = any(
            origin(home) == origin(url)
            for home in db.scalars(
                select(Source.homepage_url).where(
                    Source.homepage_url.is_not(None), Source.status.in_(["paused", "archived"])
                )
            )
        )
        if (
            (candidate and candidate.status not in ACTIVE)
            or closed_home
            or stopped
            or not matched_topics(
                " ".join(page.topics) + " " + page.title + " " + url, policy.topics
            )
            or any(matches(page.title + " " + url, word) for word in policy.excluded_terms)
        ):
            permanent = candidate and candidate.status in {"dismissed", "promoted"}
            page.status = "ignored" if permanent else "deferred"
            page.next_check_at = now + timedelta(days=1)
            db.commit()
            continue
        db.commit()
        try:
            final, data = reader.html(url)
            metadata = PageMetadata()
            metadata.feed(data.decode("utf-8", errors="replace"))
            item = parse_web(data, final)[0]
            links = page_links(data, final, policy.topics)
            same_site = [link for link in links if origin(link["url"]) == origin(final)]
            is_listing = len(same_site) >= 2 and (
                not metadata.article or metadata.article_blocks >= 2
            )
            if not links and not metadata.article:
                links = reader.sitemap_links(final, policy.topics)
                is_listing = bool(links)
            content_topics = matched_topics(item.title + " " + (item.body or ""), policy.topics)
            allowed_language = (
                not policy.languages
                or not metadata.language
                or metadata.language in policy.languages
            )
            excluded = any(
                matches(item.title + " " + (item.body or ""), word)
                for word in policy.excluded_terms
            )
            if not lease_valid():
                return
            page = db.get(SourceDiscoveryPage, identity, populate_existing=True)
            if allowed_language and not excluded:
                for link in links:
                    enqueue(
                        db,
                        link["url"],
                        link["title"],
                        content_topics or page.topics,
                        final,
                        depth + 1,
                        policy,
                    )
            candidate = db.scalar(select(SourceCandidate).where(SourceCandidate.feed_url == url))
            if candidate is None and content_topics and allowed_language and not excluded:
                kind = "web_listing" if is_listing else "article"
                count = db.scalar(
                    select(func.count())
                    .select_from(SourceCandidate)
                    .where(
                        SourceCandidate.status.in_(ACTIVE),
                        SourceCandidate.endpoint_type == "article"
                        if kind == "article"
                        else SourceCandidate.endpoint_type != "article",
                    )
                )
                from app.content_deduplication import find_duplicate_content

                already_formal = (
                    find_duplicate_content(db, url, item.title) if kind == "article" else None
                )
                covered = kind == "web_listing" and (
                    db.scalar(
                        select(SourceCandidate.id).where(
                            SourceCandidate.homepage_url == origin(final),
                            SourceCandidate.endpoint_type != "article",
                        )
                    )
                    or db.scalar(select(Source.id).where(Source.homepage_url == origin(final)))
                )
                if (
                    count < (ARTICLE_CAP if kind == "article" else policy.max_trial_sources)
                    and not already_formal
                    and not covered
                ):
                    candidate = SourceCandidate(
                        name=item.title[:200],
                        homepage_url=origin(final),
                        feed_url=url,
                        endpoint_type=kind,
                        topics=content_topics,
                        language=metadata.language,
                        discovery_kind="web_link",
                        evidence_url=evidence,
                        reason="沿真实链接发现，匹配关注方向：" + "、".join(content_topics),
                    )
                    db.add(candidate)
                    db.flush()
            # Add actual article evidence to its parent listing without extra fetches.
            targets = []
            if candidate is not None and candidate.endpoint_type == "article":
                targets.append(candidate)
            if not is_listing and content_topics and allowed_language and not excluded:
                targets.extend(
                    db.scalars(
                        select(SourceCandidate).where(
                            SourceCandidate.feed_url == evidence,
                            SourceCandidate.endpoint_type == "web_listing",
                            SourceCandidate.status.in_(ACTIVE),
                        )
                    )
                )
            work = [trial_snapshot(c) for c in targets]
            page.status, page.last_error = "visited", None
            page.attempts = 0
            page.next_check_at = now + timedelta(days=1 if is_listing else 7)
            if candidate is not None:
                candidate.check_count += 1
                candidate.success_count += 1
                candidate.last_checked_at, candidate.next_check_at = now, now + timedelta(days=1)
                candidate.consecutive_failures, candidate.last_error = 0, None
            db.commit()
            for trial in work:
                if not save_samples(db, trial, [item], policy, now, lease_valid):
                    return
        except Exception as exc:
            db.rollback()
            if not lease_valid():
                return
            page = db.get(SourceDiscoveryPage, identity, populate_existing=True)
            page.attempts += 1
            page.status = "failed"
            page.last_error = "暂时无法读取网页或网站限制自动访问，保留记录并退避重试。"
            page.next_check_at = now + timedelta(hours=min(24, 2 ** min(page.attempts, 5)))
            candidate = db.scalar(select(SourceCandidate).where(SourceCandidate.feed_url == url))
            if candidate and candidate.status in ACTIVE and not isinstance(exc, BudgetExceeded):
                candidate.check_count += 1
                candidate.consecutive_failures += 1
                candidate.last_checked_at = now
                candidate.last_error = page.last_error
                candidate.next_check_at = page.next_check_at
            db.commit()
            if isinstance(exc, BudgetExceeded) and reader.used >= REQUEST_BUDGET:
                break


def trial_snapshot(candidate):
    return SimpleNamespace(
        id=candidate.id,
        name=candidate.name,
        topics=list(candidate.topics),
        language=candidate.language,
        samples=list(candidate.samples),
        seen_urls=list(candidate.seen_urls),
    )


def save_samples(db, trial, items, policy, now, lease_valid):
    from app.config import settings
    from app.source_exploration import ACTIVE, sample_evidence

    db.commit()
    pending = sum(s.get("analysis_status") != "analyzed" for s in trial.samples) + len(items)
    allowance = min(5, policy.web_analysis_remaining)
    samples = sample_evidence(trial, items, policy, analysis_budget=allowance)
    if settings.analysis_api_base_url and settings.analysis_model:
        policy.web_analysis_remaining -= min(pending, allowance)
    if not lease_valid():
        return False
    target = db.get(SourceCandidate, trial.id, populate_existing=True)
    if target is None or target.status not in ACTIVE:
        db.commit()
        return True
    target.samples = samples
    target.seen_urls = [s["url"] for s in samples]
    target.sample_count = len(samples)
    analyzed = [s for s in samples if s["analysis_status"] == "analyzed"]
    target.analysis_count = len(analyzed)
    target.relevant_count = sum(
        bool(s.get("is_testing_relevant"))
        and s["testing_relevance_score"] >= settings.testing_relevance_threshold
        for s in analyzed
    )
    target.status = (
        "ready" if len(analyzed) >= (1 if target.endpoint_type == "article" else 5) else "trial"
    )
    target.next_check_at = now + timedelta(days=1)
    target.last_error = (
        "部分样例分析失败，后续检查时重试。"
        if any(s["analysis_status"] == "failed" for s in samples)
        else None
    )
    db.commit()
    return True
