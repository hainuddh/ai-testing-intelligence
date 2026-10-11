import smtplib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import Session

from app import email_digest as service
from app.config import settings
from app.models import ContentItem, EmailDigestDelivery, Source, User
from app.security import create_access_token

NOW = datetime(2026, 10, 10, 1, tzinfo=UTC)  # 09:00 Asia/Shanghai.


@pytest.fixture(autouse=True)
def completed_preparation(monkeypatch):
    # Delivery/recipient tests assume preparation has completed. Real collection,
    # analysis and daily research prerequisites are covered in test_digest_preparation.
    monkeypatch.setattr(
        service, "digest_preparation_pending", lambda _db, _now: None, raising=False
    )


@pytest.fixture
def subscription(db_session, monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    monkeypatch.setattr(settings, "smtp_from", "radar@example.com")
    service.save_recipient(db_session, email="reader@example.com", now=NOW - timedelta(hours=2))
    return service.update_preferences(
        db_session,
        enabled=True,
        value_threshold=70,
        send_time="09:00",
        now=NOW - timedelta(hours=2),
    )


def item(db, name, **kwargs):
    source = db.scalar(select(Source))
    if source is None:
        user = User(username="owner", password_hash="unused", role="admin")
        db.add(user)
        db.flush()
        source = Source(name="Testing Lab", source_type="blog", created_by=user.id)
        db.add(source)
        db.flush()
    fields = dict(
        source_id=source.id,
        title=name,
        url=f"https://example.com/{name}",
        analysis_status="analyzed",
        analysis_disposition="radar",
        testing_relevance_score=85,
        testing_value_score=80,
        analyzed_at=NOW - timedelta(minutes=10),
        analysis_summary="有价值的测试摘要",
        testing_value_analysis="减少回归成本",
        applicable_scenarios=["回归测试"],
        adoption_suggestions=["小规模试点"],
        fetched_at=NOW - timedelta(days=7),
    )
    fields.update(kwargs)
    obj = ContentItem(**fields)
    db.add(obj)
    db.commit()
    return obj


def run_single_digest(db, *, now):
    deliveries = service.run_daily_digest(db, now=now)
    assert len(deliveries) <= 1
    return deliveries[0] if deliveries else None


def test_strict_threshold_radar_admission_and_sorted_snapshot(db_session, subscription):
    low = item(db_session, "boundary", testing_value_score=70)
    first = item(db_session, "highest", testing_value_score=95)
    second = item(db_session, "eligible", testing_value_score=71)
    item(db_session, "watch", analysis_disposition="watch")
    item(db_session, "filtered", analysis_disposition="filtered")
    item(db_session, "pending", analysis_status="pending")
    item(db_session, "irrelevant", testing_relevance_score=20)
    item(db_session, "history", analyzed_at=NOW - timedelta(days=1))
    item(db_session, "missing-analysis-time", analyzed_at=None)
    with patch.object(service, "send_digest") as send:
        delivery = run_single_digest(db_session, now=NOW)
    assert delivery.status == "sent"
    assert [entry.content_id for entry in delivery.items] == [first.id, second.id]
    assert low.id not in [entry.content_id for entry in delivery.items]
    assert delivery.body_markdown.index("highest") < delivery.body_markdown.index("eligible")
    assert "### 场景落地建议" in delivery.body_markdown
    assert "https://example.com/highest" in delivery.body_markdown
    assert send.call_count == 1


def test_daily_schedule_empty_skip_can_send_late_analysis_same_day(db_session, subscription):
    with patch.object(service, "send_digest") as send:
        assert run_single_digest(db_session, now=NOW - timedelta(seconds=1)) is None
        empty = run_single_digest(db_session, now=NOW)
        assert empty.status == "skipped"
        late = item(db_session, "late", analyzed_at=NOW + timedelta(hours=1))
        ready = run_single_digest(db_session, now=NOW + timedelta(hours=2))
        assert ready.id == empty.id and ready.status == "sent"
        tomorrow = run_single_digest(db_session, now=NOW + timedelta(days=1))
    assert [entry.content_id for entry in ready.items] == [late.id]
    assert tomorrow.status == "skipped"
    assert send.call_count == 1


def test_restart_and_reanalysis_do_not_resend_to_same_recipient(db_session, subscription):
    obj = item(db_session, "once")
    with patch.object(service, "send_digest") as send:
        run_single_digest(db_session, now=NOW)
        with Session(db_session.get_bind()) as restarted:
            assert run_single_digest(restarted, now=NOW + timedelta(minutes=1)) is None
            obj.analyzed_at = NOW + timedelta(hours=1)
            db_session.commit()
            assert run_single_digest(restarted, now=NOW + timedelta(days=1)).status == "skipped"
    assert send.call_count == 1


def test_failed_delivery_retries_frozen_report_without_losing_new_items(db_session, subscription):
    first = item(db_session, "first")
    with patch.object(
        service, "send_digest", side_effect=smtplib.SMTPAuthenticationError(535, b"secret")
    ):
        failed = run_single_digest(db_session, now=NOW)
    assert failed.status == "failed"
    assert "secret" not in failed.error_message
    frozen = failed.body_markdown
    second = item(db_session, "second", analyzed_at=NOW + timedelta(minutes=1))
    with patch.object(service, "send_digest") as send:
        assert run_single_digest(db_session, now=NOW + timedelta(seconds=30)) is None
        retried = run_single_digest(db_session, now=NOW + timedelta(minutes=6))
        assert retried.id == failed.id and retried.status == "sent"
        assert retried.attempts == 2 and retried.body_markdown == frozen
        assert run_single_digest(db_session, now=NOW + timedelta(minutes=7)) is None
        tomorrow = run_single_digest(db_session, now=NOW + timedelta(days=1))
    assert [entry.content_id for entry in retried.items] == [first.id]
    assert [entry.content_id for entry in tomorrow.items] == [second.id]
    assert send.call_count == 2


def test_disabled_and_missing_smtp_do_not_send_or_consume_content(
    db_session, subscription, monkeypatch
):
    item(db_session, "waiting")
    subscription.enabled = False
    db_session.commit()
    with patch.object(service, "send_digest") as send:
        assert run_single_digest(db_session, now=NOW) is None
        subscription.enabled = True
        db_session.commit()
        monkeypatch.setattr(settings, "smtp_host", "")
        assert run_single_digest(db_session, now=NOW) is None
    assert db_session.scalar(select(EmailDigestDelivery)) is None
    send.assert_not_called()


def test_uncertain_delivery_is_reserved_until_explicit_retry(db_session, subscription):
    first = item(db_session, "uncertain")
    with patch.object(service, "send_digest", side_effect=service.DeliveryUncertain()):
        unknown = run_single_digest(db_session, now=NOW)
    assert unknown.status == "uncertain"
    with patch.object(service, "send_digest") as send:
        tomorrow = run_single_digest(db_session, now=NOW + timedelta(days=1))
        assert tomorrow.status == "skipped"
        send.assert_not_called()
        service.retry_delivery(db_session, unknown.id, now=NOW + timedelta(days=1))
        result = run_single_digest(db_session, now=NOW + timedelta(days=1, minutes=1))
    assert result.status == "sent"
    assert [entry.content_id for entry in result.items] == [first.id]


def test_interrupted_claim_becomes_uncertain_without_resending(db_session, subscription):
    item(db_session, "interrupted")
    with patch.object(service, "send_digest", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            run_single_digest(db_session, now=NOW)
    with patch.object(service, "send_digest") as send:
        result = run_single_digest(db_session, now=NOW + timedelta(minutes=11))
    assert result.status == "uncertain"
    send.assert_not_called()


def test_changed_recipient_starts_new_window_and_disabling_preserves_window(
    db_session, subscription
):
    from app.models import EmailDigestRecipient

    recipient = db_session.scalar(select(EmailDigestRecipient))
    start = recipient.started_at
    service.save_recipient(
        db_session, recipient_id=recipient.id, email=recipient.email, enabled=False, now=NOW
    )
    assert recipient.started_at == start
    service.save_recipient(
        db_session, recipient_id=recipient.id, email="new@example.com", enabled=True, now=NOW
    )
    assert service.utc(recipient.started_at) == NOW
    item(db_session, "old-recipient-window")
    with patch.object(service, "send_digest") as send:
        assert run_single_digest(db_session, now=NOW).status == "skipped"
    send.assert_not_called()


def admin_headers(db, role="admin"):
    user = User(username=role, password_hash="unused", role=role, is_active=True)
    db.add(user)
    db.commit()
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


def test_preferences_and_history_are_admin_only_and_do_not_expose_credentials(client, db_session):
    assert client.get("/api/v1/email-digest/preferences").status_code == 401
    viewer = admin_headers(db_session, "viewer")
    assert client.get("/api/v1/email-digest/preferences", headers=viewer).status_code == 403
    assert client.get("/api/v1/email-digest/deliveries", headers=viewer).status_code == 403
    admin = admin_headers(db_session)
    preferences = client.get("/api/v1/email-digest/preferences", headers=admin).json()
    assert preferences["enabled"] is False and preferences["value_threshold"] == 70
    assert preferences["send_time"] == "09:00"
    assert "smtp_password" not in preferences
    add_recipient(client, admin, "reader@example.com")
    result = client.put(
        "/api/v1/email-digest/preferences",
        headers=admin,
        json={
            "enabled": True,
            "value_threshold": 70,
            "send_time": "09:00",
        },
    )
    assert result.status_code == 200
    assert "recipient" not in result.json()
    assert client.get("/api/v1/email-digest/deliveries", headers=admin).json()["total"] == 0


@pytest.mark.parametrize(
    "fields",
    [
        {"send_time": "25:00"},
        {"send_time": "9:00"},
        {"value_threshold": -1},
        {"value_threshold": 101},
    ],
)
def test_preferences_validate_mailbox_time_and_score(client, db_session, fields):
    headers = admin_headers(db_session)
    add_recipient(client, headers, "reader@example.com")
    payload = dict(enabled=True, value_threshold=70, send_time="09:00")
    payload.update(fields)
    assert (
        client.put("/api/v1/email-digest/preferences", headers=headers, json=payload).status_code
        == 422
    )


def test_multipart_message_escapes_html_and_has_markdown_attachment(db_session, subscription):
    item(
        db_session,
        "safe",
        title='<script>alert("x")</script>',
        analysis_summary='<img src=x onerror="bad">',
        url="javascript:alert(1)",
    )
    with patch.object(service, "send_digest"):
        delivery = run_single_digest(db_session, now=NOW)
    message = service.build_message(delivery)
    assert message["To"] == "reader@example.com"
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "<script>" not in html and "<img" not in html
    assert "javascript:" not in html
    attachments = list(message.iter_attachments())
    assert len(attachments) == 1 and attachments[0].get_filename().endswith(".md")
    assert "### 场景落地建议" in attachments[0].get_content()


def test_smtp_starttls_auth_and_envelope(db_session, subscription, monkeypatch):
    item(db_session, "smtp")
    with patch.object(service, "send_digest"):
        delivery = run_single_digest(db_session, now=NOW)
    monkeypatch.setattr(settings, "smtp_username", "account")
    monkeypatch.setattr(settings, "smtp_password", "private-password")
    with patch.object(service.smtplib, "SMTP") as smtp:
        transport = smtp.return_value.__enter__.return_value
        transport.send_message.return_value = {}
        service.send_digest(delivery)
    transport.starttls.assert_called_once()
    transport.login.assert_called_once_with("account", "private-password")
    assert transport.send_message.call_args.kwargs["to_addrs"] == ["reader@example.com"]


def test_collection_worker_leaves_delivery_to_the_independent_scheduler(db_session):
    from app import worker

    with (
        patch.object(worker, "engine", db_session.get_bind()),
        patch.object(worker, "run_analysis") as analysis,
        patch.object(worker, "run_github_discovery"),
        patch.object(worker, "run_email_digest") as digest,
    ):
        worker.run_once()
    analysis.assert_called_once()
    digest.assert_not_called()


def test_concurrent_worker_cannot_send_while_lease_is_held(db_session, subscription):
    item(db_session, "concurrent")

    def concurrent_send(_delivery):
        with Session(db_session.get_bind()) as other:
            assert run_single_digest(other, now=NOW + timedelta(seconds=1)) is None

    with patch.object(service, "send_digest", side_effect=concurrent_send) as send:
        result = run_single_digest(db_session, now=NOW)
    assert result.status == "sent"
    assert send.call_count == 1


def test_retry_limit_and_manual_retry_api(client, db_session, subscription):
    item(db_session, "retry-limit")
    with patch.object(
        service, "send_digest", side_effect=smtplib.SMTPConnectError(421, b"unavailable")
    ) as send:
        failed = run_single_digest(db_session, now=NOW)
        run_single_digest(db_session, now=NOW + timedelta(minutes=6))
        run_single_digest(db_session, now=NOW + timedelta(minutes=22))
        assert run_single_digest(db_session, now=NOW + timedelta(hours=1)) is None
    assert failed.attempts == 3 and failed.next_attempt_at is None
    assert send.call_count == 3
    viewer = admin_headers(db_session, "viewer")
    path = f"/api/v1/email-digest/deliveries/{failed.id}/retry"
    assert client.post(path, headers=viewer).status_code == 403
    admin = admin_headers(db_session)
    assert client.post(path, headers=admin).json()["status"] == "pending"
    assert client.post(path, headers=admin).status_code == 409
    assert (
        client.post("/api/v1/email-digest/deliveries/99999/retry", headers=admin).status_code == 404
    )


def test_smtp_ssl_disconnect_and_quit_failure(db_session, subscription, monkeypatch):
    item(db_session, "ssl")
    with patch.object(service, "send_digest"):
        delivery = run_single_digest(db_session, now=NOW)
    monkeypatch.setattr(settings, "smtp_security", "ssl")
    monkeypatch.setattr(settings, "smtp_port", 465)
    with patch.object(service.smtplib, "SMTP_SSL") as smtp:
        smtp.return_value.__enter__.return_value.send_message.side_effect = (
            smtplib.SMTPServerDisconnected("secret")
        )
        with pytest.raises(service.DeliveryUncertain):
            service.send_digest(delivery)
    with patch.object(service.smtplib, "SMTP_SSL") as smtp:
        smtp.return_value.__enter__.return_value.send_message.return_value = {}
        smtp.return_value.__exit__.side_effect = smtplib.SMTPServerDisconnected("quit failed")
        service.send_digest(delivery)
        smtp.return_value.__enter__.return_value.starttls.assert_not_called()


def test_deleted_and_reimported_url_is_not_emailed_again(db_session, subscription):
    obj = item(db_session, "same-url")
    with patch.object(service, "send_digest") as send:
        run_single_digest(db_session, now=NOW)
        db_session.delete(obj)
        db_session.commit()
        item(db_session, "same-url", analyzed_at=NOW + timedelta(hours=1))
        result = run_single_digest(db_session, now=NOW + timedelta(days=1))
    assert result.status == "skipped"
    assert send.call_count == 1


def test_capacity_overflow_is_kept_for_later_digests(db_session, subscription, monkeypatch):
    monkeypatch.setattr(service, "MAX_ITEMS", 2)
    for name in ["capacity-1", "capacity-2", "capacity-3"]:
        item(db_session, name)
    with patch.object(service, "send_digest"):
        today = run_single_digest(db_session, now=NOW)
        tomorrow = run_single_digest(db_session, now=NOW + timedelta(days=1))
    assert today.item_count == 2 and tomorrow.item_count == 1
    assert {entry.content_hash for entry in today.items}.isdisjoint(
        entry.content_hash for entry in tomorrow.items
    )


def test_email_migration_upgrade_downgrade_and_model_defaults(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'email-migration.db'}"
    monkeypatch.setattr(settings, "database_url", db_url)
    api_dir = Path(__file__).resolve().parents[1]
    config = Config(str(api_dir / "alembic.ini"))
    config.set_main_option("script_location", str(api_dir / "migrations"))
    command.upgrade(config, "head")
    engine = create_engine(db_url)
    try:
        with Session(engine) as db:
            assert service.get_preferences(db).enabled is False
            assert service.get_preferences(db).send_time == "09:00"
        command.downgrade(config, "20261009_01_github_exploration")
        assert "email_digest_preferences" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        with Session(engine) as db:
            assert service.get_preferences(db).value_threshold == 70
    finally:
        engine.dispose()


def add_recipient(client, headers, email, **fields):
    response = client.post(
        "/api/v1/email-digest/recipients",
        headers=headers,
        json={"email": email, **fields},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_recipients_crud_validation_duplicates_and_admin_permissions(client, db_session):
    base = "/api/v1/email-digest/recipients"
    assert client.get(base).status_code == 401
    viewer = admin_headers(db_session, "viewer")
    assert client.get(base, headers=viewer).status_code == 403
    assert client.post(base, headers=viewer, json={"email": "a@example.com"}).status_code == 403
    admin = admin_headers(db_session)
    first = add_recipient(client, admin, " Reader@EXAMPLE.com ", name=" 测试组 ")
    second = add_recipient(client, admin, "second@example.com", enabled=False)
    assert first["email"] == "reader@example.com" and first["name"] == "测试组"
    assert first["enabled"] and first["started_at"] is None
    assert len(client.get(base, headers=admin).json()) == 2
    assert client.post(base, headers=admin, json={"email": "READER@example.com"}).status_code == 409
    assert client.post(base, headers=admin, json={"email": "bad"}).status_code == 422
    assert (
        client.post(
            base, headers=admin, json={"email": "a@example.com\r\nBcc:b@example.com"}
        ).status_code
        == 422
    )
    path = f"{base}/{first['id']}"
    assert client.patch(path, headers=viewer, json={"enabled": False}).status_code == 403
    assert client.delete(path, headers=viewer).status_code == 403
    assert client.patch(path, headers=admin, json={"email": second["email"]}).status_code == 409
    assert client.patch(path, headers=admin, json={"email": ""}).status_code == 422
    assert client.patch(path, headers=admin, json={"name": "x" * 101}).status_code == 422
    assert client.patch(path, headers=admin, json={"enabled": None}).status_code == 422
    changed = client.patch(
        path,
        headers=admin,
        json={"email": "edited@example.com", "name": "新名称", "enabled": False},
    )
    assert changed.status_code == 200 and changed.json()["enabled"] is False
    assert changed.json()["email"] == "edited@example.com" and changed.json()["name"] == "新名称"
    assert client.delete(path, headers=admin).status_code == 204
    assert client.delete(path, headers=admin).status_code == 404
    assert client.patch(f"{base}/99999", headers=admin, json={"enabled": True}).status_code == 404


def test_global_preferences_require_an_enabled_recipient(client, db_session):
    admin = admin_headers(db_session)
    payload = {"enabled": True, "value_threshold": 70, "send_time": "09:00"}
    path = "/api/v1/email-digest/preferences"
    assert client.put(path, headers=admin, json=payload).status_code == 422
    recipient = add_recipient(client, admin, "reader@example.com", enabled=False)
    assert client.put(path, headers=admin, json=payload).status_code == 422
    client.patch(
        f"/api/v1/email-digest/recipients/{recipient['id']}", headers=admin, json={"enabled": True}
    )
    result = client.put(path, headers=admin, json=payload)
    assert result.status_code == 200 and result.json()["enabled"] is True
    assert "recipient" not in result.json()
    assert client.get("/api/v1/email-digest/recipients", headers=admin).json()[0]["started_at"]


@pytest.mark.parametrize(
    "email",
    [
        "",
        "bad",
        "a@example.com,b@example.com",
        ".a@example.com",
        "a..b@example.com",
        "a@example.com\n",
    ],
)
def test_recipients_reject_invalid_mailboxes(client, db_session, email):
    admin = admin_headers(db_session)
    assert (
        client.post(
            "/api/v1/email-digest/recipients", headers=admin, json={"email": email}
        ).status_code
        == 422
    )


def test_global_pause_resume_preserves_each_recipient_window(db_session, subscription):
    from app.models import EmailDigestRecipient

    first = db_session.scalar(select(EmailDigestRecipient))
    start = first.started_at
    service.update_preferences(
        db_session, enabled=False, value_threshold=75, send_time="10:30", now=NOW
    )
    second = service.save_recipient(db_session, email="second@example.com", now=NOW)
    assert second.started_at is None and first.started_at == start
    service.update_preferences(
        db_session, enabled=True, value_threshold=70, send_time="09:00", now=NOW
    )
    assert first.started_at == start and service.utc(second.started_at) == NOW


def test_per_recipient_lease_does_not_block_other_mailboxes(db_session, subscription):
    item(db_session, "concurrent-recipients")
    service.save_recipient(db_session, email="second@example.com", now=NOW - timedelta(hours=1))
    sent = []

    def concurrent_send(delivery):
        sent.append(delivery.recipient)
        if delivery.recipient == "reader@example.com":
            with Session(db_session.get_bind()) as other:
                results = service.run_daily_digest(other, now=NOW + timedelta(seconds=1))
                assert len(results) == 1 and results[0].recipient == "second@example.com"

    with patch.object(service, "send_digest", side_effect=concurrent_send):
        results = service.run_daily_digest(db_session, now=NOW)
    assert len(results) == 1 and results[0].recipient == "reader@example.com"
    assert sent == ["reader@example.com", "second@example.com"]


def test_each_recipient_uses_current_time_after_a_slow_previous_delivery(db_session, subscription):
    item(db_session, "slow-queue")
    service.save_recipient(db_session, email="second@example.com", now=NOW - timedelta(hours=1))
    later = NOW + timedelta(minutes=20)
    with patch.object(service, "datetime", wraps=datetime) as clock:
        clock.now.side_effect = [NOW, later]
        with patch.object(service, "send_digest"):
            results = service.run_daily_digest(db_session)
    assert len(results) == 2
    assert service.utc(results[1].created_at) == later


def test_multiple_recipients_send_independently_and_failure_isolated(
    client, db_session, subscription
):
    admin = admin_headers(db_session)
    second = add_recipient(client, admin, "second@example.com")
    add_recipient(client, admin, "paused@example.com", enabled=False)
    obj = item(db_session, "everyone", analyzed_at=datetime.now(UTC) + timedelta(minutes=1))
    send_time = datetime.now(UTC) + timedelta(days=1)

    def send(delivery):
        if delivery.recipient == "reader@example.com":
            raise smtplib.SMTPConnectError(421, b"unavailable")

    with patch.object(service, "send_digest", side_effect=send) as mocked:
        results = service.run_daily_digest(db_session, now=send_time)
    assert {x.recipient: x.status for x in results} == {
        "reader@example.com": "failed",
        second["email"]: "sent",
    }
    assert mocked.call_count == 2
    assert all([entry.content_id for entry in delivery.items] == [obj.id] for delivery in results)
    assert all(
        str(service.build_message(delivery)["To"]) == delivery.recipient for delivery in results
    )
    with patch.object(service, "send_digest") as send:
        retried = service.run_daily_digest(db_session, now=send_time + timedelta(minutes=6))
        assert len(retried) == 1 and retried[0].recipient == "reader@example.com"
        assert service.run_daily_digest(db_session, now=send_time + timedelta(minutes=7)) == []
    assert send.call_count == 1


def test_recipient_windows_edit_pause_and_reenable(client, db_session, subscription):
    admin = admin_headers(db_session)
    recipient = add_recipient(client, admin, "new@example.com", enabled=False)
    path = f"/api/v1/email-digest/recipients/{recipient['id']}"
    assert recipient["started_at"] is None
    enabled = client.patch(path, headers=admin, json={"enabled": True}).json()
    start = enabled["started_at"]
    assert start
    assert client.patch(path, headers=admin, json={"name": "标记"}).json()["started_at"] == start
    assert client.patch(path, headers=admin, json={"enabled": False}).json()["started_at"] == start
    assert client.patch(path, headers=admin, json={"enabled": True}).json()["started_at"] == start
    edited = client.patch(path, headers=admin, json={"email": "changed@example.com"}).json()
    assert edited["started_at"] != start
    assert (
        client.patch(path, headers=admin, json={"email": "CHANGED@EXAMPLE.COM"}).json()[
            "started_at"
        ]
        == edited["started_at"]
    )


def test_deleted_recipient_keeps_history_and_dedup_on_readd(client, db_session, subscription):
    admin = admin_headers(db_session)
    obj = item(db_session, "receipt-stays")
    with patch.object(service, "send_digest"):
        delivery = service.run_daily_digest(db_session, now=NOW)[0]
    recipient = client.get("/api/v1/email-digest/recipients", headers=admin).json()[0]
    path = f"/api/v1/email-digest/recipients/{recipient['id']}"
    assert client.delete(path, headers=admin).status_code == 204
    assert client.get("/api/v1/email-digest/deliveries", headers=admin).json()["total"] == 1
    assert (
        client.post(
            f"/api/v1/email-digest/deliveries/{delivery.id}/retry", headers=admin
        ).status_code
        == 409
    )
    with patch.object(service, "send_digest") as send:
        assert service.run_daily_digest(db_session, now=NOW + timedelta(days=1)) == []
        add_recipient(client, admin, "reader@example.com")
        obj.analyzed_at = datetime.now(UTC) + timedelta(minutes=1)
        db_session.commit()
        result = service.run_daily_digest(db_session, now=datetime.now(UTC) + timedelta(days=1))
    assert result[0].status == "skipped"
    send.assert_not_called()


def test_retry_only_for_active_recipient_and_history_filter(client, db_session, subscription):
    admin = admin_headers(db_session)
    item(db_session, "paused-retry")
    with patch.object(service, "send_digest", side_effect=service.DeliveryUncertain()):
        delivery = service.run_daily_digest(db_session, now=NOW)[0]
    recipient = client.get("/api/v1/email-digest/recipients", headers=admin).json()[0]
    path = f"/api/v1/email-digest/recipients/{recipient['id']}"
    client.patch(path, headers=admin, json={"enabled": False})
    retry = f"/api/v1/email-digest/deliveries/{delivery.id}/retry"
    assert client.post(retry, headers=admin).status_code == 409
    client.patch(path, headers=admin, json={"enabled": True})
    assert client.post(retry, headers=admin).status_code == 200
    assert (
        client.get(
            "/api/v1/email-digest/deliveries?recipient=READER@example.com", headers=admin
        ).json()["total"]
        == 1
    )
    assert (
        client.get(
            "/api/v1/email-digest/deliveries?recipient=other@example.com", headers=admin
        ).json()["total"]
        == 0
    )
    assert (
        client.get("/api/v1/email-digest/deliveries?recipient=bad", headers=admin).status_code
        == 422
    )


def test_recipient_migration_preserves_legacy_mailbox_window_and_receipts(tmp_path, monkeypatch):
    db_url = f"sqlite:///{tmp_path / 'recipient-migration.db'}"
    monkeypatch.setattr(settings, "database_url", db_url)
    api_dir = Path(__file__).resolve().parents[1]
    config = Config(str(api_dir / "alembic.ini"))
    config.set_main_option("script_location", str(api_dir / "migrations"))
    command.upgrade(config, "20261010_01_email_digest")
    engine = create_engine(db_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO email_digest_preferences "
                    "(id, enabled, recipient, value_threshold, send_time, started_at) "
                    "VALUES (1, 1, ' Reader@EXAMPLE.com ', 75, '10:30', '2026-10-10 01:00:00')"
                )
            )
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        with Session(engine) as db:
            from app.models import EmailDigestRecipient

            recipient = db.scalar(select(EmailDigestRecipient))
            assert recipient.email == "reader@example.com" and recipient.enabled
            assert service.utc(recipient.started_at) == NOW
            preference = service.get_preferences(db)
            assert (
                preference.enabled
                and preference.value_threshold == 75
                and preference.send_time == "10:30"
            )
            item(db, "legacy-receipt", analyzed_at=NOW + timedelta(minutes=1))
            monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
            monkeypatch.setattr(settings, "smtp_from", "radar@example.com")
            with patch.object(service, "send_digest"):
                assert (
                    service.run_daily_digest(db, now=NOW + timedelta(hours=2))[0].status == "sent"
                )
        command.downgrade(config, "20261010_01_email_digest")
        assert "email_digest_recipients" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        with Session(engine) as db, patch.object(service, "send_digest") as send:
            assert (
                service.run_daily_digest(db, now=NOW + timedelta(days=1, hours=2))[0].status
                == "skipped"
            )
            send.assert_not_called()
    finally:
        engine.dispose()
