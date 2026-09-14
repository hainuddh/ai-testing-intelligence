import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlparse, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx
from sqlalchemy.orm import Session

from app.cache import delete_prefix_sync
from app.config import settings
from app.content_deduplication import content_url_hash, find_duplicate_content
from app.models import ContentItem, FetchRun, SourceEndpoint

SUMMARY_ENRICHMENT_LIMIT = 3


@dataclass
class FetchedItem:
    title: str
    url: str
    summary: str | None = None
    body: str | None = None
    published_at: datetime | None = None


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.description: str | None = None
        self.ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self.ignored_depth += 1
        if tag == "title":
            self.in_title = True
        if tag == "meta":
            values = {name.lower(): value for name, value in attrs if value is not None}
            field = (values.get("name") or values.get("property") or "").lower()
            if field in {"description", "og:description", "twitter:description"}:
                description = values.get("content", "").strip()
                if description and self.description is None:
                    self.description = description

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = False
        if tag in {"script", "style", "noscript", "svg"} and self.ignored_depth:
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.ignored_depth:
            return
        text = data.strip()
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
        self.text_parts.append(text)


class TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.text_parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.text_parts.append(text)


def plain_text(value: str | None) -> str | None:
    if not value:
        return None
    parser = TextParser()
    parser.feed(value)
    return " ".join(parser.text_parts).strip() or None


class FeedDiscoveryParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "link":
            return
        values = {name.lower(): value for name, value in attrs if value is not None}
        relations = values.get("rel", "").lower().split()
        content_type = values.get("type", "").lower().split(";", 1)[0]
        href = values.get("href")
        if "alternate" in relations and content_type in {
            "application/rss+xml",
            "application/atom+xml",
        } and href:
            self.urls.append(urljoin(self.base_url, href))


class RelatedLinkParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__()
        self.base_url = base_url
        self.content_depth = 0
        self.ignored_depth = 0
        self.current_href: str | None = None
        self.current_title = ""
        self.current_text: list[str] = []
        self.current_in_content = False
        self.links: list[tuple[str, str, bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg", "nav", "header", "footer"}:
            self.ignored_depth += 1
        if tag in {"article", "main"}:
            self.content_depth += 1
        if tag != "a" or self.ignored_depth:
            return
        values = {name.lower(): value for name, value in attrs if value is not None}
        href = values.get("href")
        if href:
            self.current_href = href
            self.current_title = values.get("title", "")
            self.current_text = []
            self.current_in_content = self.content_depth > 0

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self.current_href is not None:
            title = " ".join(self.current_text).strip() or self.current_title.strip()
            if title and len(self.links) < 500:
                self.links.append(
                    (
                        title,
                        urljoin(self.base_url, self.current_href),
                        self.current_in_content,
                    )
                )
            self.current_href = None
            self.current_title = ""
            self.current_text = []
            self.current_in_content = False
        if tag in {"article", "main"} and self.content_depth:
            self.content_depth -= 1
        if (
            tag in {"script", "style", "noscript", "svg", "nav", "header", "footer"}
            and self.ignored_depth
        ):
            self.ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.current_href is not None and not self.ignored_depth:
            text = " ".join(data.split())
            if text:
                self.current_text.append(text)


RELATED_LINK_SKIP_TEXT = re.compile(
    r"^(?:home|homepage|login|log in|sign in|sign up|register|more|read more|click here|"
    r"首页|主页|登录|注册|更多|阅读全文|点击这里|关于我们|联系我们|投稿)$",
    re.IGNORECASE,
)
RELATED_LINK_SKIP_PATH = re.compile(
    r"/(?:login|signin|signup|register|account|about|contact|privacy|terms|search)(?:/|$)",
    re.IGNORECASE,
)
RELATED_LINK_SKIP_EXTENSION = re.compile(
    r"\.(?:avif|css|gif|ico|jpe?g|js|png|svg|webp|woff2?)(?:$|\?)", re.IGNORECASE
)
RELATED_LINK_SKIP_HOSTS = {
    "facebook.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
}


def normalized_link_url(url: str) -> str | None:
    if (
        len(url) > 2048
        or "\\" in url
        or any(ord(character) < 32 or ord(character) == 127 for character in url)
    ):
        return None
    decoded = unquote(url)
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
        return None
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if hostname == "localhost" or hostname.endswith(".local"):
        return None
    try:
        if not ipaddress.ip_address(hostname).is_global:
            return None
    except ValueError:
        pass
    if ":" in hostname:
        hostname = f"[{hostname}]"
    authority = f"{hostname}:{port}" if port is not None else hostname
    query = urlencode(
        [
            (name, value)
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not name.lower().startswith("utm_")
        ]
    )
    return urlunsplit((parsed.scheme.lower(), authority, parsed.path or "/", query, ""))


def extract_related_links(
    data: bytes, page_url: str, relevance_terms: list[str], limit: int = 5
) -> list[dict[str, str]]:
    parser = RelatedLinkParser(page_url)
    parser.feed(data.decode("utf-8", errors="replace"))
    page_identity = normalized_link_url(page_url)
    page_hostname = urlparse(page_url).hostname
    has_content_links = any(in_content for _, _, in_content in parser.links)
    terms = {
        token.lower()
        for value in relevance_terms
        for token in re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{2,}|[\u4e00-\u9fff]{2,8}", value)
    }
    candidates: list[tuple[int, int, dict[str, str]]] = []
    seen: set[str] = set()
    for position, (title, raw_url, in_content) in enumerate(parser.links):
        title = " ".join(title.split())[:200]
        url = normalized_link_url(raw_url)
        if (
            not url
            or url == page_identity
            or url in seen
            or RELATED_LINK_SKIP_TEXT.fullmatch(title)
            or RELATED_LINK_SKIP_PATH.search(urlparse(url).path)
            or RELATED_LINK_SKIP_EXTENSION.search(url)
            or (has_content_links and not in_content)
        ):
            continue
        seen.add(url)
        hostname = urlparse(url).hostname
        normalized_hostname = (hostname or "").removeprefix("www.")
        score = 10 if in_content else 0
        if normalized_hostname in RELATED_LINK_SKIP_HOSTS:
            continue
        if hostname and page_hostname and hostname != page_hostname:
            score += 2
        lowered_title = title.lower()
        score += min(3, sum(term in lowered_title for term in terms))
        candidates.append((score, position, {"title": title, "url": url}))
    candidates.sort(key=lambda candidate: (-candidate[0], candidate[1]))
    return [candidate[2] for candidate in candidates[:limit]]


def feed_candidates(data: bytes, page_url: str) -> list[str]:
    parser = FeedDiscoveryParser(page_url)
    parser.feed(data.decode("utf-8", errors="replace"))
    parsed = urlparse(page_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    path_base = page_url.split("?", 1)[0].rstrip("/") + "/"
    candidates = [
        *parser.urls,
        urljoin(path_base, "rss/"),
        urljoin(path_base, "feed/"),
        f"{origin}/rss.xml",
        f"{origin}/feed.xml",
        f"{origin}/atom.xml",
    ]
    return list(dict.fromkeys(candidate for candidate in candidates if candidate != page_url))[:6]


def validated_connection_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only HTTP and HTTPS URLs are supported")
    try:
        default_port = 443 if parsed.scheme == "https" else 80
        addresses = socket.getaddrinfo(
            parsed.hostname, parsed.port or default_port, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise ValueError("Endpoint hostname could not be resolved") from exc
    public_ips = []
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise ValueError("Endpoint resolves to a non-public address")
        public_ips.append(ip)
    ip = public_ips[0]
    host = f"[{ip}]" if ip.version == 6 else str(ip)
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    return f"{parsed.scheme}://{host}{port}{path}", parsed.hostname


def validate_public_url(url: str) -> None:
    validated_connection_url(url)


def download(url: str) -> tuple[str, bytes, str]:
    current_url = url
    started = time.monotonic()
    with httpx.Client(
        timeout=settings.fetch_timeout_seconds, follow_redirects=False, trust_env=False
    ) as client:
        for _ in range(6):
            connection_url, hostname = validated_connection_url(current_url)
            parsed = urlparse(current_url)
            host_header = hostname
            if parsed.port:
                host_header = f"{host_header}:{parsed.port}"
            with client.stream(
                "GET",
                connection_url,
                headers={"Host": host_header, "User-Agent": "ATI-Fetcher/0.1"},
                extensions={"sni_hostname": hostname.encode()},
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("Redirect response has no location")
                    current_url = urljoin(current_url, location)
                    continue
                response.raise_for_status()
                chunks = []
                size = 0
                for chunk in response.iter_bytes():
                    if time.monotonic() - started > settings.fetch_timeout_seconds:
                        raise ValueError("Fetch exceeded the configured total timeout")
                    size += len(chunk)
                    if size > settings.fetch_max_bytes:
                        raise ValueError("Response exceeds the configured size limit")
                    chunks.append(chunk)
                return current_url, b"".join(chunks), response.headers.get("content-type", "")
    raise ValueError("Too many redirects")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def namespace(tag: str) -> str | None:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else None


def child_text(element: ElementTree.Element, *names: str) -> str | None:
    for name in names:
        for child in element:
            if (
                local_name(child.tag) == name
                and namespace(child.tag) != "http://search.yahoo.com/mrss/"
                and child.text
            ):
                return child.text.strip()
    return None


def parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
        return parsed.replace(tzinfo=parsed.tzinfo or UTC)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            return parsed.replace(tzinfo=parsed.tzinfo or UTC)
        except (TypeError, ValueError):
            return None


def parse_feed(data: bytes, base_url: str, limit: int) -> list[FetchedItem]:
    root = ElementTree.fromstring(data)
    entries = [node for node in root.iter() if local_name(node.tag) in {"item", "entry"}]
    items = []
    for entry in entries[:limit]:
        title = child_text(entry, "title") or "Untitled"
        link = child_text(entry, "link")
        if not link:
            link_node = next((node for node in entry if local_name(node.tag) == "link"), None)
            link = link_node.get("href") if link_node is not None else None
        if not link:
            continue
        items.append(
            FetchedItem(
                title=title[:500],
                url=urljoin(base_url, link),
                summary=plain_text(
                    child_text(entry, "summary", "description", "content", "encoded")
                ),
                published_at=parse_date(
                    child_text(entry, "published", "updated", "pubdate")
                ),
            )
        )
    return items


def download_feed(
    url: str, limit: int, discovery_url: str | None = None
) -> tuple[str, list[FetchedItem]]:
    discovery_pages: list[tuple[bytes, str]] = []
    try:
        final_url, data, _content_type = download(url)
        try:
            items = parse_feed(data, final_url, limit)
        except ElementTree.ParseError:
            items = []
        if items:
            return final_url, items
        discovery_pages.append((data, final_url))
    except (httpx.HTTPError, ValueError):
        if not discovery_url or discovery_url == url:
            raise

    if discovery_url and all(page_url != discovery_url for _, page_url in discovery_pages):
        try:
            homepage_url, homepage_data, _content_type = download(discovery_url)
            discovery_pages.append((homepage_data, homepage_url))
        except (httpx.HTTPError, ValueError):
            pass

    candidates = list(
        dict.fromkeys(
            candidate
            for page_data, page_url in discovery_pages
            for candidate in feed_candidates(page_data, page_url)
            if candidate not in {url, discovery_url}
        )
    )[:6]
    for candidate in candidates:
        try:
            candidate_url, candidate_data, _content_type = download(candidate)
            items = parse_feed(candidate_data, candidate_url, limit)
        except (ElementTree.ParseError, httpx.HTTPError, ValueError):
            continue
        if items:
            return candidate_url, items
    raise ValueError("No valid RSS or Atom feed could be discovered")


def parse_web(data: bytes, url: str) -> list[FetchedItem]:
    parser = PageParser()
    parser.feed(data.decode("utf-8", errors="replace"))
    title = " ".join(parser.title_parts).strip() or urlparse(url).hostname or "Untitled"
    body = " ".join(parser.text_parts)
    summary = parser.description or body[:1000] or None
    return [FetchedItem(title=title[:500], url=url, summary=summary, body=body or None)]


def fetch_endpoint(db: Session, endpoint: SourceEndpoint) -> FetchRun:
    run = FetchRun(endpoint_id=endpoint.id, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        endpoint_changed = False
        if endpoint.endpoint_type == "rss":
            final_url, items = download_feed(
                endpoint.url,
                endpoint.max_items_per_run,
                endpoint.source.homepage_url,
            )
            if final_url != endpoint.url:
                endpoint.url = final_url
                endpoint_changed = True
        elif endpoint.endpoint_type == "web":
            final_url, data, _content_type = download(endpoint.url)
            items = parse_web(data, final_url)
        else:
            raise ValueError(f"Endpoint type {endpoint.endpoint_type!r} is not fetchable yet")
        created = 0
        updated = False
        enrichment_attempts = 0
        for item in items:
            url_hash = content_url_hash(item.url)
            duplicate_title = item.title if endpoint.endpoint_type == "rss" else None
            existing = find_duplicate_content(db, item.url, duplicate_title)
            needs_summary = existing is None or (
                existing.url_hash == url_hash and not existing.summary
            )
            if (
                endpoint.endpoint_type == "rss"
                and not item.summary
                and needs_summary
                and enrichment_attempts < SUMMARY_ENRICHMENT_LIMIT
            ):
                enrichment_attempts += 1
                try:
                    page_url, page_data, _content_type = download(item.url)
                    item.summary = parse_web(page_data, page_url)[0].summary
                except (httpx.HTTPError, ValueError):
                    pass
            if existing is not None:
                if endpoint.endpoint_type == "web" and existing.url_hash == url_hash:
                    existing.title = item.title
                    existing.summary = item.summary
                    existing.body = item.body
                    existing.fetched_at = datetime.now(UTC)
                    existing.analysis_status = "pending"
                    existing.analysis_attempts = 0
                    existing.related_links = []
                    existing.related_links_extracted_at = None
                    existing.analysis_error = None
                    existing.next_analysis_at = None
                    updated = True
                elif (
                    existing.url_hash == url_hash
                    and existing.source_id == endpoint.source_id
                    and item.summary
                    and existing.summary != item.summary
                ):
                    existing.summary = item.summary
                    updated = True
                continue
            db.add(
                ContentItem(
                    source_id=endpoint.source_id,
                    title=item.title,
                    url=item.url,
                    url_hash=url_hash,
                    summary=item.summary,
                    body=item.body,
                    published_at=item.published_at,
                )
            )
            created += 1
        endpoint.health_status = "healthy"
        run.status = "succeeded"
        run.items_created = created
        run.completed_at = datetime.now(UTC)
        db.commit()
        if updated:
            delete_prefix_sync("content:list")
            delete_prefix_sync("collected:list")
        if endpoint_changed:
            delete_prefix_sync("sources:endpoints")
    except Exception as exc:
        db.rollback()
        run = db.get(FetchRun, run.id)
        endpoint = db.get(SourceEndpoint, endpoint.id)
        if run is None or endpoint is None:
            raise
        endpoint.health_status = "unhealthy"
        run.status = "failed"
        run.error = str(exc)[:2000]
        run.completed_at = datetime.now(UTC)
        db.commit()
    db.refresh(run)
    return run
