"""Independent, bounded delivery worker; never performs collection or research."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.database import engine
from app.email_digest import LEASE_DURATION, MAX_ATTEMPTS, send_queued_digest
from app.models import EmailDigestDelivery, EmailDigestPreference, EmailDigestRecipient

logger = logging.getLogger(__name__)


def queued_recipient_ids(db: Session, now: datetime) -> list[int]:
    preference = db.get(EmailDigestPreference, 1)
    if not preference or not preference.enabled:
        return []
    due = (
        select(EmailDigestDelivery.id)
        .where(
            func.lower(EmailDigestDelivery.recipient) == EmailDigestRecipient.email,
            or_(
                and_(
                    EmailDigestDelivery.status.in_(["pending", "failed"]),
                    EmailDigestDelivery.attempts < MAX_ATTEMPTS,
                    or_(
                        EmailDigestDelivery.next_attempt_at <= now,
                        and_(
                            EmailDigestDelivery.status == "pending",
                            EmailDigestDelivery.next_attempt_at.is_(None),
                        ),
                    ),
                ),
                and_(
                    EmailDigestDelivery.status == "sending",
                    EmailDigestDelivery.attempted_at <= now - LEASE_DURATION,
                ),
            ),
        )
        .exists()
    )
    return list(
        db.scalars(
            select(EmailDigestRecipient.id)
            .where(
                EmailDigestRecipient.enabled,
                due,
            )
            .order_by(EmailDigestRecipient.id)
        )
    )


def send_recipient(recipient_id: int) -> None:
    try:
        with Session(engine) as db:
            send_queued_digest(db, recipient_id)
    except Exception:
        logger.error("Delivery check failed for recipient_id=%s; will retry", recipient_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("Independent mail sender started")
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="mail-delivery") as executor:
        futures = {}
        while True:
            with Session(engine) as db:
                ids = queued_recipient_ids(db, datetime.now(UTC))
            for recipient_id in ids:
                if sum(not future.done() for future in futures.values()) >= 4:
                    break
                previous = futures.get(recipient_id)
                if previous is None or previous.done():
                    futures[recipient_id] = executor.submit(send_recipient, recipient_id)
            for recipient_id in list(futures):
                if recipient_id not in ids and futures[recipient_id].done():
                    del futures[recipient_id]
            time.sleep(min(30, 60 - time.time() % 60))


if __name__ == "__main__":
    main()
