"""Daily content digests with durable snapshots, receipts and a worker lease."""

import logging
import re
import smtplib
import ssl
from datetime import UTC, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from html import escape
from uuid import uuid4

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, defer, selectinload

from app.config import settings
from app.models import (
    ContentItem,
    EmailDigestDelivery,
    EmailDigestGitHubItem,
    EmailDigestItem,
    EmailDigestPreference,
    EmailDigestRecipient,
    FetchRun,
    GitHubExplorationState,
    GitHubRepo,
    GitHubReport,
    GitHubReportItem,
    SourceEndpoint,
)
from app.reporting import inline_text, render_markdown_report, safe_url

SHANGHAI = timezone(timedelta(hours=8))
LEASE_DURATION = timedelta(minutes=10)
MAX_ATTEMPTS = 3
MAX_ITEMS = 100
logger = logging.getLogger(__name__)


class RecipientConflict(ValueError):
    """A mailbox is already present, including concurrent additions."""


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
    value_threshold: int,
    send_time: str,
    now: datetime | None = None,
) -> EmailDigestPreference:
    if not 0 <= value_threshold <= 100 or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", send_time):
        raise ValueError("价值分或发送时间无效")
    preference = get_preferences(db)
    recipients = db.scalars(
        select(EmailDigestRecipient).where(EmailDigestRecipient.enabled.is_(True))
    ).all()
    if enabled and not recipients:
        raise ValueError("启用邮件推送前请添加并启用至少一个收件邮箱")
    if enabled:
        for recipient in recipients:
            if recipient.started_at is None:
                recipient.started_at = utc(now or datetime.now(UTC))
    preference.enabled = enabled
    preference.value_threshold = value_threshold
    preference.send_time = send_time
    db.commit()
    db.refresh(preference)
    return preference


def save_recipient(
    db: Session,
    *,
    email: str,
    name: str = "",
    enabled: bool = True,
    recipient_id: int | None = None,
    now: datetime | None = None,
) -> EmailDigestRecipient:
    email = normalize_mailbox(email).lower()
    name = name.strip()
    if len(name) > 100:
        raise ValueError("邮箱名称或备注不能超过100字")
    preference = get_preferences(db)
    recipient = db.get(EmailDigestRecipient, recipient_id) if recipient_id is not None else None
    if recipient_id is not None and recipient is None:
        raise LookupError("收件邮箱不存在")
    duplicate = db.scalar(select(EmailDigestRecipient).where(EmailDigestRecipient.email == email))
    if duplicate is not None and duplicate.id != recipient_id:
        raise RecipientConflict("该收件邮箱已存在")
    if recipient is None:
        recipient = EmailDigestRecipient(email=email)
        db.add(recipient)
    elif recipient.email != email:
        recipient.started_at = None
    if enabled and preference.enabled and recipient.started_at is None:
        recipient.started_at = utc(now or datetime.now(UTC))
    recipient.email = email
    recipient.name = name
    recipient.enabled = enabled
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise RecipientConflict("该收件邮箱已存在") from exc
    db.refresh(recipient)
    return recipient


def delete_recipient(db: Session, recipient_id: int) -> None:
    recipient = db.get(EmailDigestRecipient, recipient_id)
    if recipient is None:
        raise LookupError("收件邮箱不存在")
    # Deliveries and URL receipts intentionally have no FK to this management row.
    db.delete(recipient)
    db.commit()


def digest_day_start(now: datetime) -> datetime:
    return (
        utc(now)
        .astimezone(SHANGHAI)
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .astimezone(UTC)
    )


def needs_daily_preparation(db: Session, now: datetime) -> bool:
    preference = db.get(EmailDigestPreference, 1)
    if not preference or not preference.enabled:
        return False
    local = utc(now).astimezone(SHANGHAI)
    if local.strftime("%H:%M") < preference.send_time:
        return False
    finished = (
        select(EmailDigestDelivery.id)
        .where(
            func.lower(EmailDigestDelivery.recipient) == EmailDigestRecipient.email,
            EmailDigestDelivery.status != "skipped",
            or_(
                EmailDigestDelivery.business_date == local.strftime("%Y-%m-%d"),
                EmailDigestDelivery.sent_at >= digest_day_start(now),
            ),
        )
        .exists()
    )
    return (
        db.scalar(
            select(EmailDigestRecipient.id)
            .where(EmailDigestRecipient.enabled.is_(True), ~finished)
            .limit(1)
        )
        is not None
    )


def github_research_pending(db: Session, now: datetime) -> bool:
    state = db.get(GitHubExplorationState, 1, populate_existing=True)
    start = digest_day_start(now)
    if (
        state is None
        or state.status != "completed"
        or state.started_at is None
        or state.finished_at is None
        or not start <= utc(state.started_at) <= utc(state.finished_at) <= utc(now)
    ):
        return True
    if state.report_id is not None:
        report = db.get(GitHubReport, state.report_id)
        return (
            report is None
            or report.status != "ready"
            or report.report_type != "daily"
            or not start <= utc(report.generated_at) <= utc(now)
        )
    return False  # A completed discovery with no new eligible projects is genuinely empty.


def digest_preparation_pending(db: Session, now: datetime) -> str | None:
    start = digest_day_start(now)
    latest_run = (
        select(FetchRun.id)
        .where(FetchRun.endpoint_id == SourceEndpoint.id)
        .order_by(FetchRun.started_at.desc(), FetchRun.id.desc())
        .limit(1)
        .correlate(SourceEndpoint)
        .scalar_subquery()
    )
    runs = db.execute(
        select(FetchRun.status, FetchRun.started_at, FetchRun.completed_at)
        .select_from(SourceEndpoint)
        .outerjoin(FetchRun, FetchRun.id == latest_run)
        .where(SourceEndpoint.enabled.is_(True))
    ).all()
    if any(
        status != "succeeded"
        or started is None
        or completed is None
        or not start <= utc(started) <= utc(completed) <= utc(now)
        for status, started, completed in runs
    ):
        return "当天信源采集尚未全部成功完成"
    if (
        db.scalar(select(ContentItem.id).where(ContentItem.analysis_status != "analyzed").limit(1))
        is not None
    ):
        return "内容分析尚未全部完成，等待分析或失败重试"
    if github_research_pending(db, now):
        return "当天GitHub项目研究尚未完成，等待研究或失败重试"
    return None


def digest_content(
    db: Session, preference: EmailDigestPreference, recipient: EmailDigestRecipient, now: datetime
) -> list[ContentItem]:
    reserved = (
        select(EmailDigestItem.id)
        .join(EmailDigestDelivery)
        .where(
            func.lower(EmailDigestDelivery.recipient) == recipient.email,
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
                ContentItem.analyzed_at >= recipient.started_at,
                ContentItem.analyzed_at <= now,
                ~reserved,
            )
            .order_by(
                ContentItem.testing_value_score.desc(), ContentItem.analyzed_at, ContentItem.id
            )
            .limit(MAX_ITEMS)
        )
    )


def digest_github_research(
    db: Session, recipient: EmailDigestRecipient, now: datetime
) -> list[tuple[int, GitHubRepo]]:
    reserved = (
        select(EmailDigestGitHubItem.id)
        .join(EmailDigestDelivery)
        .where(
            func.lower(EmailDigestDelivery.recipient) == recipient.email,
            EmailDigestGitHubItem.repo_key == func.lower(GitHubRepo.full_name),
        )
        .exists()
    )
    rows = db.execute(
        select(GitHubReport.id, GitHubRepo)
        .join(GitHubReportItem, GitHubReportItem.report_id == GitHubReport.id)
        .join(GitHubRepo, GitHubRepo.id == GitHubReportItem.repo_id)
        .where(
            GitHubReport.report_type == "daily",
            GitHubReport.status == "ready",
            GitHubReport.generated_at >= recipient.started_at,
            GitHubReport.generated_at <= now,
            ~reserved,
        )
        .order_by(
            GitHubRepo.testing_value_score.desc(),
            GitHubReport.generated_at,
            GitHubReportItem.rank,
            GitHubRepo.id,
        )
        .limit(MAX_ITEMS)
    ).all()
    selected = {}
    for report_id, repo in rows:
        selected.setdefault(repo.full_name.lower(), (report_id, repo))
    return list(selected.values())


def render_summary(
    items: list[ContentItem], business_date: str, repos: list[GitHubRepo] | None = None
) -> tuple[str, str]:
    repos = repos or []
    title = f"每日高价值测试情报 · {business_date} · {len(items) + len(repos)}条"
    text = [title, ""]
    html = [
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><body>',
        f"<h1>{escape(title)}</h1>",
    ]
    if items:
        text.extend(["内容情报", ""])
        html.append("<h2>内容情报</h2>")
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
    if repos:
        text.extend(["GitHub项目研究日报", ""])
        html.append("<h2>GitHub项目研究日报</h2>")
    for index, repo in enumerate(repos, start=1):
        heading = f"{index}. {inline_text(repo.full_name)}"
        metadata = f"测试价值 {repo.testing_value_score}/100 · 星数 {repo.stars}"
        summary = repo.intel_summary or repo.summary or repo.description or "暂无摘要"
        suggestions = repo.adoption_suggestions or []
        text.extend([heading, metadata, summary, "落地建议：", *[f"- {x}" for x in suggestions]])
        html.extend(
            [
                f"<h3>{escape(heading)}</h3><p>{escape(metadata)}</p><p>{escape(summary)}</p>",
                "<h4>落地建议</h4><ul>",
                *[f"<li>{escape(x)}</li>" for x in suggestions],
                "</ul>",
            ]
        )
        url = safe_url(repo.html_url)
        if url:
            text.append(f"仓库：{url}")
            html.append(f'<p><a href="{escape(url, quote=True)}">查看仓库</a></p>')
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
    recipient: EmailDigestRecipient,
    now: datetime,
) -> EmailDigestDelivery | None:
    interrupted = db.scalar(
        select(EmailDigestDelivery)
        .where(
            func.lower(EmailDigestDelivery.recipient) == recipient.email,
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
            func.lower(EmailDigestDelivery.recipient) == recipient.email,
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
    day_start = digest_day_start(now)
    if local.strftime("%H:%M") < preference.send_time:
        return None
    today = local.strftime("%Y-%m-%d")
    existing = db.scalar(
        select(EmailDigestDelivery).where(
            func.lower(EmailDigestDelivery.recipient) == recipient.email,
            or_(
                EmailDigestDelivery.business_date == today, EmailDigestDelivery.sent_at >= day_start
            ),
        )
    )
    if existing and existing.status != "skipped":
        return None
    reason = digest_preparation_pending(db, now)
    if reason:
        logger.info("Email digest waiting for preparation: %s", reason)
        return None
    items = digest_content(db, preference, recipient, now)
    research = digest_github_research(db, recipient, now)
    # Keep both categories represented when a backlog reaches the per-mail limit.
    research = research[: MAX_ITEMS // 2 if items else MAX_ITEMS]
    items = items[: MAX_ITEMS - len(research)]
    repos = [repo for _, repo in research]
    item_count = len(items) + len(repos)
    if not item_count and existing:
        return None
    body_text, body_html = render_summary(items, today, repos)
    markdown = render_markdown_report(items, generated_at=now) if items else ""
    if repos:
        from app.github_service import render_daily_markdown

        markdown += "\n\n" + render_daily_markdown(f"GitHub项目研究日报 · {today}", repos)
    delivery = existing or EmailDigestDelivery(recipient=recipient.email, business_date=today)
    delivery.status = "pending" if item_count else "skipped"
    delivery.created_at = now
    delivery.subject = f"每日高价值测试情报 · {today} · {item_count}条"
    delivery.body_text = body_text if item_count else ""
    delivery.body_html = body_html if item_count else ""
    delivery.body_markdown = markdown if item_count else ""
    delivery.item_count = item_count
    delivery.items = [
        EmailDigestItem(content_id=obj.id, content_hash=obj.url_hash, rank=i)
        for i, obj in enumerate(items, start=1)
    ]
    delivery.github_items = [
        EmailDigestGitHubItem(report_id=report_id, repo_key=repo.full_name.lower(), rank=i)
        for i, (report_id, repo) in enumerate(research, start=1)
    ]
    db.add(delivery)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return deliver(db, delivery, now) if item_count else delivery


def run_recipient_digest(
    db: Session, recipient_id: int, now: datetime
) -> EmailDigestDelivery | None:
    token = str(uuid4())
    claimed = db.execute(
        update(EmailDigestRecipient)
        .where(
            EmailDigestRecipient.id == recipient_id,
            EmailDigestRecipient.enabled.is_(True),
            or_(
                EmailDigestRecipient.lease_until.is_(None),
                EmailDigestRecipient.lease_until <= now,
            ),
        )
        .values(lease_token=token, lease_until=now + LEASE_DURATION)
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if claimed.rowcount != 1:
        return None
    try:
        preference = get_preferences(db)
        db.refresh(preference)
        recipient = db.get(EmailDigestRecipient, recipient_id, populate_existing=True)
        if not preference.enabled or recipient is None or not recipient.enabled:
            return None
        if recipient.started_at is None:
            recipient.started_at = now
            db.commit()
        return run_claimed_digest(db, preference, recipient, now)
    finally:
        db.rollback()
        db.execute(
            update(EmailDigestRecipient)
            .where(
                EmailDigestRecipient.id == recipient_id,
                EmailDigestRecipient.lease_token == token,
            )
            .values(lease_token=None, lease_until=None)
        )
        db.commit()


def run_daily_digest(db: Session, *, now: datetime | None = None) -> list[EmailDigestDelivery]:
    fixed_time = utc(now) if now is not None else None
    preference = get_preferences(db)
    if not preference.enabled or not smtp_configured():
        return []
    recipient_ids = db.scalars(
        select(EmailDigestRecipient.id)
        .where(EmailDigestRecipient.enabled.is_(True))
        .order_by(EmailDigestRecipient.id)
    ).all()
    deliveries = []
    for recipient_id in recipient_ids:
        # A slow mailbox must not leave later mailboxes with already expired leases.
        delivery = run_recipient_digest(db, recipient_id, fixed_time or datetime.now(UTC))
        if delivery is not None:
            deliveries.append(delivery)
    return deliveries


def retry_delivery(
    db: Session, delivery_id: int, *, now: datetime | None = None
) -> EmailDigestDelivery:
    now = utc(now or datetime.now(UTC))
    delivery = db.get(EmailDigestDelivery, delivery_id)
    preference = get_preferences(db)
    if delivery is None:
        raise LookupError("发送记录不存在")
    recipient = db.scalar(
        select(EmailDigestRecipient).where(
            EmailDigestRecipient.email == delivery.recipient.strip().lower(),
            EmailDigestRecipient.enabled.is_(True),
        )
    )
    if not preference.enabled or recipient is None:
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
