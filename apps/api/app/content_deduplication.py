import hashlib

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import ContentItem


def content_url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def find_duplicate_content(
    db: Session, url: str, title: str | None = None
) -> ContentItem | None:
    url_hash = content_url_hash(url)
    duplicate_conditions = [ContentItem.url_hash == url_hash]
    if title is not None:
        duplicate_conditions.append(
            func.lower(func.trim(ContentItem.title)) == title.strip().lower()
        )
    return db.scalar(
        select(ContentItem)
        .where(or_(*duplicate_conditions))
        .order_by((ContentItem.url_hash == url_hash).desc())
        .limit(1)
    )
