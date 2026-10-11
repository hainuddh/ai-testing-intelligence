from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select

from app.dependencies import AdminUser, DbSession
from app.email_digest import (
    get_preferences,
    normalize_mailbox,
    retry_delivery,
    smtp_configured,
    update_preferences,
)
from app.models import EmailDigestDelivery

router = APIRouter(prefix="/email-digest", tags=["email-digest"])


class PreferenceUpdate(BaseModel):
    enabled: bool = False
    recipient: str = Field(default="", max_length=254)
    value_threshold: int = Field(default=70, ge=0, le=100)
    send_time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    @field_validator("recipient")
    @classmethod
    def valid_mailbox(cls, value: str) -> str:
        return normalize_mailbox(value) if value else ""

    @model_validator(mode="after")
    def enabled_has_recipient(self):
        if self.enabled and not self.recipient:
            raise ValueError("启用邮件推送前请填写收件邮箱")
        return self


class PreferenceResponse(PreferenceUpdate):
    model_config = ConfigDict(from_attributes=True)
    started_at: datetime | None = None
    timezone: str = "Asia/Shanghai"
    smtp_configured: bool


class DeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    recipient: str
    business_date: str
    status: str
    item_count: int
    attempts: int
    created_at: datetime
    sent_at: datetime | None
    next_attempt_at: datetime | None
    error_message: str | None


class DeliveryListResponse(BaseModel):
    items: list[DeliveryResponse]
    total: int


def preferences_response(db: DbSession) -> PreferenceResponse:
    obj = get_preferences(db)
    return PreferenceResponse(
        enabled=obj.enabled,
        recipient=obj.recipient,
        value_threshold=obj.value_threshold,
        send_time=obj.send_time,
        started_at=obj.started_at,
        smtp_configured=smtp_configured(),
    )


@router.get("/preferences", response_model=PreferenceResponse)
def read_preferences(db: DbSession, _user: AdminUser) -> PreferenceResponse:
    return preferences_response(db)


@router.put("/preferences", response_model=PreferenceResponse)
def save_preferences(
    payload: PreferenceUpdate, db: DbSession, _user: AdminUser
) -> PreferenceResponse:
    update_preferences(db, **payload.model_dump())
    return preferences_response(db)


@router.get("/deliveries", response_model=DeliveryListResponse)
def list_deliveries(
    db: DbSession,
    _user: AdminUser,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> DeliveryListResponse:
    total = db.scalar(select(func.count(EmailDigestDelivery.id))) or 0
    items = db.scalars(
        select(EmailDigestDelivery)
        .order_by(EmailDigestDelivery.created_at.desc(), EmailDigestDelivery.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return DeliveryListResponse(
        items=[DeliveryResponse.model_validate(obj) for obj in items],
        total=total,
    )


@router.post("/deliveries/{delivery_id}/retry", response_model=DeliveryResponse)
def queue_retry(delivery_id: int, db: DbSession, _user: AdminUser) -> DeliveryResponse:
    try:
        delivery = retry_delivery(db, delivery_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return DeliveryResponse.model_validate(delivery)
