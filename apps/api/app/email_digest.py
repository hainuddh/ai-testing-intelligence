"""Daily content digests with durable snapshots, receipts and a worker lease."""

import re
import smtplib
import ssl
from datetime import UTC, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from html import escape
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer, selectinload

from app.config import settings
from app.models import ContentItem, EmailDigestDelivery, EmailDigestItem, EmailDigestPreference
from app.reporting import inline_text, render_markdown_report, safe_url

SHANGHAI = timezone(timedelta(hours=8))
LEASE_DURATION = timedelta(minutes=10)
MAX_ATTEMPTS = 3
MAX_ITEMS = 100


class DeliveryUncertain(Exception):
    """The server might have accepted DATA; automatic retry could duplicate it."""


def utc(value: datetime) -> datetime:
    return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC)


def normalize_mailbox(value: str) -> str:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("邮箱不能包含控制字符")
    value = value.strip()
    local, separator, domain = value.rpartition("@")
    if (
        not separator
        or len(value) > 254
        or len(local) > 64
        or not re.fullmatch(r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+", local)
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or not re.fullmatch(
            r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
            r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",
            domain,
        )
    ):
        raise ValueError("请输入一个有效的收件邮箱")
    return f"{local}@{domain.lower()}"


def smtp_configured() -> bool:
    try:
        normalize_mailbox(settings.smtp_from)
    except ValueError:
        return False
    return bool(
        settings.smtp_host.strip()
        and settings.smtp_security in {"starttls", "ssl"}
        and 1 <= settings.smtp_port <= 65535
        and 0 < settings.smtp_timeout_seconds <= 120
        and bool(settings.smtp_username) == bool(settings.smtp_password)
    )


def get_preferences(db: Session) -> EmailDigestPreference:
    preference = db.get(EmailDigestPreference, 1)
    if preference is None:
        preference = EmailDigestPreference(id=1)
        db.add(preference)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            preference = db.get(EmailDigestPreference, 1)
    return preference


def update_preferences(
    db: Session,
    *,
    enabled: bool,
    recipient: str,
    value_threshold: int,
    send_time: str,
    now: datetime | None = None,
) -> EmailDigestPreference:
    recipient = normalize_mailbox(recipient) if recipient.strip() else ""
    if enabled and not recipient:
        raise ValueError("启用邮件推送前请填写收件邮箱")
    if not 0 <= value_threshold <= 100 or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", send_time):
        raise ValueError("价值分或发送时间无效")
    preference = get_preferences(db)
    if recipient != preference.recipient or (enabled and preference.started_at is None):
        preference.started_at = utc(now or datetime.now(UTC)) if enabled else None
    preference.enabled = enabled
    preference.recipient = recipient
    preference.value_threshold = value_threshold
    preference.send_time = send_time
    db.commit()
    db.refresh(preference)
    return preference


def digest_content(
    db: Session, preference: EmailDigestPreference, now: datetime
) -> list[ContentItem]:
    reserved = (
        select(EmailDigestItem.id)
        .join(EmailDigestDelivery)
        .where(
            EmailDigestDelivery.recipient == preference.recipient,
            EmailDigestItem.content_hash == ContentItem.url_hash,
        )
        .exists()
    )
    return list(
        db.scalars(
            select(ContentItem)
            .options(selectinload(ContentItem.source), defer(ContentItem.body))
            .where(
                ContentItem.analysis_status == "analyzed",
                or_(
                    ContentItem.analysis_disposition == "radar",
                    ContentItem.analysis_disposition.is_(None),
                ),
                ContentItem.testing_relevance_score >= settings.testing_relevance_threshold,
                ContentItem.testing_value_score > preference.value_threshold,
                ContentItem.analyzed_at >= preference.started_at,
                ContentItem.analyzed_at <= now,
                ~reserved,
            )
            .order_by(
                ContentItem.testing_value_score.desc(), ContentItem.analyzed_at, ContentItem.id
            )
            .limit(MAX_ITEMS)
        )
    )


def render_summary(items: list[ContentItem], business_date: str) -> tuple[str, str]:
    title = f"每日高价值测试情报 · {business_date} · {len(items)}条"
    text = [title, ""]
    html = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body>',
        f"<h1>{escape(title)}</h1>",
    ]
    for index, item in enumerate(items, start=1):
        heading = f"{index}. {inline_text(item.title)}"
        metadata = f"测试价值 {item.testing_value_score}/100 · 来源：{item.source.name}"
        summary = item.analysis_summary or "暂无摘要"
        suggestions = item.adoption_suggestions or []
        text.extend([heading, metadata, summary, "落地建议：", *[f"- {x}" for x in suggestions]])
        html.extend(
            [
                f"<h2>{escape(heading)}</h2><p>{escape(metadata)}</p>",
                f"<p>{escape(summary)}</p><h3>落地建议</h3><ul>",
                *[f"<li>{escape(x)}</li>" for x in suggestions],
                "</ul>",
            ]
        )
        url = safe_url(item.url)
        if url:
            text.append(f"原文：{url}")
            html.append(f'<p><a href="{escape(url, quote=True)}">查看原文</a></p>')
        text.append("")
    text.append("完整价值分析、适用场景及风险见随信 Markdown 附件。")
    html.append("<p>完整价值分析、适用场景及风险见随信 Markdown 附件。</p></body></html>")
    return "\n".join(text), "\n".join(html)


def build_message(delivery: EmailDigestDelivery) -> EmailMessage:
    sender = normalize_mailbox(settings.smtp_from)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = normalize_mailbox(delivery.recipient)
    message["Subject"] = delivery.subject
    message["Date"] = format_datetime(utc(delivery.created_at))
    message["Message-ID"] = (
        f"<signal-atlas-digest-{delivery.id}-{delivery.business_date}@{sender.split('@')[1]}>"
    )
    message.set_content(delivery.body_text)
    message.add_alternative(delivery.body_html, subtype="html")
    message.add_attachment(
        delivery.body_markdown.encode("utf-8"),
        maintype="text",
        subtype="markdown",
        params={"charset": "utf-8"},
        filename=f"testing-intelligence-{delivery.business_date}.md",
    )
    return message


def send_digest(delivery: EmailDigestDelivery) -> None:
    message = build_message(delivery)
    context = ssl.create_default_context()
    transport_class = smtplib.SMTP_SSL if settings.smtp_security == "ssl" else smtplib.SMTP
    kwargs = {"timeout": settings.smtp_timeout_seconds}
    if settings.smtp_security == "ssl":
        kwargs["context"] = context
    accepted = False
    try:
        with transport_class(settings.smtp_host, settings.smtp_port, **kwargs) as transport:
            if settings.smtp_security == "starttls":
                transport.starttls(context=context)
                transport.ehlo()
            if settings.smtp_username:
                transport.login(settings.smtp_username, settings.smtp_password)
            try:
                refused = transport.send_message(
                    message, from_addr=str(message["From"]), to_addrs=[delivery.recipient]
                )
                if refused:
                    raise smtplib.SMTPRecipientsRefused(refused)
            except (
                smtplib.SMTPResponseException,
                smtplib.SMTPRecipientsRefused,
                smtplib.SMTPNotSupportedError,
            ):
                raise
            except Exception as exc:
                raise DeliveryUncertain() from exc
            accepted = True
    except Exception:
        # QUIT failure after successful DATA must not turn acceptance into a retry.
        if not accepted:
            raise


def deliver(db: Session, delivery: EmailDigestDelivery, now: datetime) -> EmailDigestDelivery:
    delivery.status = "sending"
    delivery.attempted_at = now
    delivery.attempts += 1
    delivery.next_attempt_at = None
    db.commit()  # Persist the attempt before any network I/O.
    try:
        send_digest(delivery)
    except DeliveryUncertain:
        delivery.status = "uncertain"
        delivery.error_message = "无法确认邮件是否已接收，请核对收件箱后决定是否重试。"
    except Exception:
        delivery.status = "failed"
        delivery.error_message = "发件服务连接、认证或投递失败，请检查发件配置。"
        if delivery.attempts < MAX_ATTEMPTS:
            delivery.next_attempt_at = now + timedelta(minutes=5 * 3 ** (delivery.attempts - 1))
    else:
        delivery.status = "sent"
        delivery.sent_at = now
        delivery.error_message = None
    db.commit()
    return delivery


def run_claimed_digest(
    db: Session,
    preference: EmailDigestPreference,
    now: datetime,
) -> EmailDigestDelivery | None:
    interrupted = db.scalar(
        select(EmailDigestDelivery)
        .where(
            EmailDigestDelivery.recipient == preference.recipient,
            EmailDigestDelivery.status == "sending",
            EmailDigestDelivery.attempted_at <= now - LEASE_DURATION,
        )
        .order_by(EmailDigestDelivery.id)
    )
    if interrupted:
        interrupted.status = "uncertain"
        interrupted.error_message = "发送过程被中断，请核对收件箱后决定是否重试。"
        db.commit()
        return interrupted
    retry = db.scalar(
        select(EmailDigestDelivery)
        .where(
            EmailDigestDelivery.recipient == preference.recipient,
            EmailDigestDelivery.status.in_(["pending", "failed"]),
            EmailDigestDelivery.attempts < MAX_ATTEMPTS,
            or_(
                EmailDigestDelivery.next_attempt_at <= now, EmailDigestDelivery.status == "pending"
            ),
        )
        .order_by(EmailDigestDelivery.id)
    )
    if retry:
        return deliver(db, retry, now)
    local = now.astimezone(SHANGHAI)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    if local.strftime("%H:%M") < preference.send_time:
        return None
    today = local.strftime("%Y-%m-%d")
    existing = db.scalar(
        select(EmailDigestDelivery.id).where(
            EmailDigestDelivery.recipient == preference.recipient,
            or_(
                EmailDigestDelivery.business_date == today, EmailDigestDelivery.sent_at >= day_start
            ),
        )
    )
    if existing:
        return None
    items = digest_content(db, preference, now)
    body_text, body_html = render_summary(items, today)
    delivery = EmailDigestDelivery(
        recipient=preference.recipient,
        business_date=today,
        status="pending" if items else "skipped",
        created_at=now,
        subject=f"每日高价值测试情报 · {today} · {len(items)}条",
        body_text=body_text if items else "",
        body_html=body_html if items else "",
        body_markdown=render_markdown_report(items, generated_at=now) if items else "",
        item_count=len(items),
        items=[
            EmailDigestItem(content_id=obj.id, content_hash=obj.url_hash, rank=i)
            for i, obj in enumerate(items, start=1)
        ],
    )
    db.add(delivery)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return deliver(db, delivery, now) if items else delivery


def run_daily_digest(db: Session, *, now: datetime | None = None) -> EmailDigestDelivery | None:
    now = utc(now or datetime.now(UTC))
    preference = get_preferences(db)
    if (
        not preference.enabled
        or not preference.recipient
        or not preference.started_at
        or not smtp_configured()
    ):
        return None
    token = str(uuid4())
    claimed = db.execute(
        update(EmailDigestPreference)
        .where(
            EmailDigestPreference.id == 1,
            EmailDigestPreference.enabled.is_(True),
            or_(
                EmailDigestPreference.lease_until.is_(None),
                EmailDigestPreference.lease_until <= now,
            ),
        )
        .values(lease_token=token, lease_until=now + LEASE_DURATION)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if claimed.rowcount != 1:
        return None
    try:
        db.refresh(preference)
        return run_claimed_digest(db, preference, now)
    finally:
        db.rollback()
        db.execute(
            update(EmailDigestPreference)
            .where(
                EmailDigestPreference.id == 1,
                EmailDigestPreference.lease_token == token,
            )
            .values(lease_token=None, lease_until=None)
        )
        db.commit()


def retry_delivery(
    db: Session, delivery_id: int, *, now: datetime | None = None
) -> EmailDigestDelivery:
    now = utc(now or datetime.now(UTC))
    delivery = db.get(EmailDigestDelivery, delivery_id)
    preference = get_preferences(db)
    if delivery is None:
        raise LookupError("发送记录不存在")
    if not preference.enabled or delivery.recipient != preference.recipient:
        raise ValueError("请先启用此收件邮箱的邮件推送")
    changed = db.execute(
        update(EmailDigestDelivery)
        .where(
            EmailDigestDelivery.id == delivery_id,
            EmailDigestDelivery.status.in_(["failed", "uncertain"]),
        )
        .values(status="pending", attempts=0, error_message=None, next_attempt_at=now)
    )
    if changed.rowcount != 1:
        db.rollback()
        raise ValueError("只有失败或待确认的邮件可以重试")
    db.commit()
    db.refresh(delivery)
    return delivery
