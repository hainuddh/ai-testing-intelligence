from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select

from app.dependencies import AdminUser, DbSession
from app.digest_batches import batch_progress
from app.email_digest import (
    RecipientConflict,
    delete_recipient,
    get_preferences,
    normalize_mailbox,
    retry_delivery,
    save_recipient,
    smtp_configured,
    update_preferences,
)
from app.models import DigestBatch, EmailDigestDelivery, EmailDigestRecipient

router = APIRouter(prefix="/email-digest", tags=["email-digest"])


class PreferenceUpdate(BaseModel):
    enabled: bool = False
    value_threshold: int = Field(default=70, ge=0, le=100)
    send_time: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class PreferenceResponse(PreferenceUpdate):
    model_config = ConfigDict(from_attributes=True)
    timezone: str = "Asia/Shanghai"
    smtp_configured: bool


class RecipientCreate(BaseModel):
    email: str = Field(max_length=254)
    name: str = Field(default="", max_length=100)
    enabled: bool = True

    @field_validator("email")
    @classmethod
    def valid_mailbox(cls, value: str) -> str:
        return normalize_mailbox(value).lower()

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return value.strip()


class RecipientUpdate(BaseModel):
    email: str | None = Field(default=None, max_length=254)
    name: str | None = Field(default=None, max_length=100)
    enabled: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_null_fields(cls, data):
        if isinstance(data, dict) and any(value is None for value in data.values()):
            raise ValueError("邮箱字段不能为null")
        return data

    @field_validator("email")
    @classmethod
    def valid_mailbox(cls, value: str) -> str:
        return normalize_mailbox(value).lower()


class RecipientResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str
    name: str
    enabled: bool
    started_at: datetime | None
    created_at: datetime


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
        value_threshold=obj.value_threshold,
        send_time=obj.send_time,
        smtp_configured=smtp_configured(),
    )


@router.get("/preferences", response_model=PreferenceResponse)
def read_preferences(db: DbSession, _user: AdminUser) -> PreferenceResponse:
    return preferences_response(db)


@router.put("/preferences", response_model=PreferenceResponse)
def save_preferences(
    payload: PreferenceUpdate, db: DbSession, _user: AdminUser
) -> PreferenceResponse:
    try:
        update_preferences(db, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return preferences_response(db)


@router.get("/recipients", response_model=list[RecipientResponse])
def list_recipients(db: DbSession, _user: AdminUser) -> list[EmailDigestRecipient]:
    return list(db.scalars(select(EmailDigestRecipient).order_by(EmailDigestRecipient.id)))


@router.get("/batches")
def list_batches(db: DbSession, _user: AdminUser) -> dict:
    items = db.scalars(select(DigestBatch).order_by(DigestBatch.business_date.desc()).limit(7))
    return {"items": [batch_progress(db, batch) for batch in items]}


@router.post("/recipients", response_model=RecipientResponse, status_code=201)
def create_recipient(
    payload: RecipientCreate, db: DbSession, _user: AdminUser
) -> EmailDigestRecipient:
    try:
        return save_recipient(db, **payload.model_dump())
    except RecipientConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/recipients/{recipient_id}", response_model=RecipientResponse)
def edit_recipient(
    recipient_id: int, payload: RecipientUpdate, db: DbSession, _user: AdminUser
) -> EmailDigestRecipient:
    recipient = db.get(EmailDigestRecipient, recipient_id)
    if recipient is None:
        raise HTTPException(status_code=404, detail="收件邮箱不存在")
    fields = {"email": recipient.email, "name": recipient.name, "enabled": recipient.enabled}
    fields.update(payload.model_dump(exclude_unset=True))
    try:
        return save_recipient(db, recipient_id=recipient_id, **fields)
    except RecipientConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/recipients/{recipient_id}", status_code=204)
def remove_recipient(recipient_id: int, db: DbSession, _user: AdminUser) -> Response:
    try:
        delete_recipient(db, recipient_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=204)


@router.get("/deliveries", response_model=DeliveryListResponse)
def list_deliveries(
    db: DbSession,
    _user: AdminUser,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    recipient: str | None = Query(default=None, max_length=254),
) -> DeliveryListResponse:
    conditions = []
    if recipient is not None:
        try:
            mailbox = normalize_mailbox(recipient).lower()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        conditions.append(func.lower(EmailDigestDelivery.recipient) == mailbox)
    total = db.scalar(select(func.count(EmailDigestDelivery.id)).where(*conditions)) or 0
    items = db.scalars(
        select(EmailDigestDelivery)
        .where(*conditions)
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
