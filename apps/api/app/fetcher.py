import hashlib
import ipaddress
import socket
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ContentItem, FetchRun, SourceEndpoint


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

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text:
            return
        if self.in_title:
            self.title_parts.append(text)
        self.text_parts.append(text)


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


def child_text(element: ElementTree.Element, *names: str) -> str | None:
    wanted = set(names)
    for child in element:
        if local_name(child.tag) in wanted and child.text:
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
                summary=child_text(entry, "summary", "description", "content"),
                published_at=parse_date(
                    child_text(entry, "published", "updated", "pubdate")
                ),
            )
        )
    return items


def parse_web(data: bytes, url: str) -> list[FetchedItem]:
    parser = PageParser()
    parser.feed(data.decode("utf-8", errors="replace"))
    title = " ".join(parser.title_parts).strip() or urlparse(url).hostname or "Untitled"
    body = " ".join(parser.text_parts)
    return [FetchedItem(title=title[:500], url=url, summary=body[:1000] or None, body=body or None)]


def fetch_endpoint(db: Session, endpoint: SourceEndpoint) -> FetchRun:
    run = FetchRun(endpoint_id=endpoint.id, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    try:
        final_url, data, content_type = download(endpoint.url)
        if endpoint.endpoint_type == "rss" or "xml" in content_type:
            items = parse_feed(data, final_url, endpoint.max_items_per_run)
        elif endpoint.endpoint_type == "web":
            items = parse_web(data, final_url)
        else:
            raise ValueError(f"Endpoint type {endpoint.endpoint_type!r} is not fetchable yet")
        created = 0
        for item in items:
            url_hash = hashlib.sha256(item.url.encode()).hexdigest()
            existing = db.scalar(select(ContentItem).where(ContentItem.url_hash == url_hash))
            if existing is not None:
                if endpoint.endpoint_type == "web":
                    existing.title = item.title
                    existing.summary = item.summary
                    existing.body = item.body
                    existing.fetched_at = datetime.now(UTC)
                    existing.analysis_status = "pending"
                    existing.analysis_attempts = 0
                    existing.analysis_error = None
                    existing.next_analysis_at = None
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
