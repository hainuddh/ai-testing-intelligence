from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from app.dependencies import CurrentUser, DbSession, MaintainerUser
from app.models import SourceCandidate, SourceExplorationPreference
from app.source_exploration import (
    ACTIVE,
    SCOPE_NOTE,
    clean_terms,
    matched_topics,
    preferences,
    promote_candidate,
)

router = APIRouter(prefix="/source-exploration", tags=["source-exploration"])


class PreferenceUpdate(BaseModel):
    enabled: bool = True
    topics: list[str] = Field(default_factory=list, max_length=20)
    languages: list[str] = Field(default_factory=lambda: ["zh", "en"], max_length=10)
    excluded_terms: list[str] = Field(default_factory=list, max_length=30)
    max_trial_sources: int = Field(default=3, ge=1, le=5)

    @field_validator("topics", "languages", "excluded_terms")
    @classmethod
    def normalize(cls, values: list[str]) -> list[str]:
        if any(len(value.strip()) > 100 for value in values):
            raise ValueError("Each term must be at most 100 characters")
        return clean_terms(values)


class PreferenceResponse(PreferenceUpdate):
    model_config = ConfigDict(from_attributes=True)
    last_started_at: datetime | None = None
    last_completed_at: datetime | None = None
    next_run_at: datetime | None = None
    last_error: str | None = None
    scope_note: str = SCOPE_NOTE


class CandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    homepage_url: str
    feed_url: str
    topics: list[str]
    language: str
    discovery_kind: str
    evidence_url: str
    reason: str
    status: str
    samples: list[dict]
    sample_count: int
    relevant_count: int
    analysis_count: int
    last_checked_at: datetime | None
    next_check_at: datetime | None
    last_error: str | None
    promoted_source_id: int | None


class CandidateAction(BaseModel):
    action: Literal["pause", "resume", "dismiss"]


@router.get("/preferences", response_model=PreferenceResponse)
def read_preferences(db: DbSession, _user: CurrentUser):
    return preferences(db)


@router.put("/preferences", response_model=PreferenceResponse)
def update_preferences(payload: PreferenceUpdate, db: DbSession, _user: MaintainerUser):
    pref = preferences(db)
    db.refresh(pref, with_for_update=True)
    for name, value in payload.model_dump(exclude_unset=True).items():
        setattr(pref, name, value)
    pref.next_run_at = None
    pref.lease_token, pref.lease_until = None, None  # Invalidate stale in-flight results.
    kept = 0
    for trial in db.scalars(
        select(SourceCandidate)
        .where(SourceCandidate.status.in_(ACTIVE))
        .order_by(SourceCandidate.created_at, SourceCandidate.id)
    ):
        description = " ".join((trial.name, trial.homepage_url, *trial.topics))
        outside_scope = (
            (bool(pref.topics) and not matched_topics(description, pref.topics))
            or (
                bool(pref.languages)
                and bool(trial.language)
                and trial.language not in pref.languages
            )
            or bool(matched_topics(description, pref.excluded_terms))
        )
        if outside_scope or kept >= pref.max_trial_sources:
            trial.status = "paused"
            trial.last_error = "探索范围或试运行上限已调整，此候选已暂停；已有样例仍保留。"
        else:
            kept += 1
    db.commit()
    db.refresh(pref)
    return pref


@router.get("/candidates")
def list_candidates(
    db: DbSession,
    _user: CurrentUser,
    status: Literal["trial", "ready", "paused", "dismissed", "promoted"] | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    include_closed: bool = False,
):
    criteria = (
        [SourceCandidate.status == status]
        if status
        else ([] if include_closed else [SourceCandidate.status.not_in(["dismissed", "promoted"])])
    )
    total = db.scalar(select(func.count()).select_from(SourceCandidate).where(*criteria))
    items = db.scalars(
        select(SourceCandidate)
        .where(*criteria)
        .order_by(SourceCandidate.created_at.desc(), SourceCandidate.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return {
        "items": [CandidateResponse.model_validate(row) for row in items],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


@router.post("/candidates/{candidate_id}/promote")
def promote(candidate_id: int, db: DbSession, user: MaintainerUser):
    result = promote_candidate(db, candidate_id, user.id)
    return {
        "candidate": CandidateResponse.model_validate(result["candidate"]),
        "source_id": result["source_id"],
    }


@router.post("/candidates/{candidate_id}/action", response_model=CandidateResponse)
def act(candidate_id: int, payload: CandidateAction, db: DbSession, _user: MaintainerUser):
    preferences(db)
    pref = db.scalar(
        select(SourceExplorationPreference)
        .where(SourceExplorationPreference.id == 1)
        .with_for_update()
    )
    candidate = db.scalar(
        select(SourceCandidate).where(SourceCandidate.id == candidate_id).with_for_update()
    )
    if candidate is None:
        raise HTTPException(status_code=404, detail="试运行信源不存在，请刷新后重试。")
    if candidate.status == "promoted":
        raise HTTPException(status_code=409, detail="此信源已正式启用，请在信源列表中管理。")
    if payload.action == "resume":
        active_count = db.scalar(
            select(func.count())
            .select_from(SourceCandidate)
            .where(SourceCandidate.status.in_(ACTIVE), SourceCandidate.id != candidate.id)
        )
        if active_count >= pref.max_trial_sources:
            raise HTTPException(
                status_code=409, detail="已达到同时试运行上限，请先暂停其他试运行。"
            )
        candidate.status = "ready" if candidate.analysis_count >= 5 else "trial"
        candidate.next_check_at = None
        pref.next_run_at = None
    else:
        candidate.status = "paused" if payload.action == "pause" else "dismissed"
    db.commit()
    db.refresh(candidate)
    return candidate
