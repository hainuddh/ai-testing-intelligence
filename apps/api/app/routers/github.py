from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, select

from app.config import settings
from app.dependencies import CurrentUser, DbSession, MaintainerUser
from app.github_service import discover_candidates, generate_daily_report, set_repo_status
from app.models import GitHubRepo, GitHubReport
from app.schemas import (
    GitHubDiscoverRequest,
    GitHubDiscoverResponse,
    GitHubRepoListResponse,
    GitHubRepoResponse,
    GitHubRepoStatusUpdate,
    GitHubReportListResponse,
    GitHubReportResponse,
)

router = APIRouter(prefix="/github", tags=["github"])


@router.post("/discover", response_model=GitHubDiscoverResponse)
def discover(
    payload: GitHubDiscoverRequest,
    db: DbSession,
    _user: MaintainerUser,
) -> GitHubDiscoverResponse:
    languages = payload.languages or settings.github_discovery_languages_list
    count = discover_candidates(db, languages)
    return GitHubDiscoverResponse(discovered=count)


@router.get("/repos", response_model=GitHubRepoListResponse)
def list_repos(
    db: DbSession,
    _user: CurrentUser,
    status_filter: str | None = Query(default=None, alias="status"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> GitHubRepoListResponse:
    stmt = select(GitHubRepo)
    if status_filter:
        stmt = stmt.where(GitHubRepo.status == status_filter)
    else:
        stmt = stmt.where(GitHubRepo.status != "ignored")
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    items = db.scalars(
        stmt.order_by(GitHubRepo.momentum_score.desc(), GitHubRepo.stars.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return GitHubRepoListResponse(
        items=[GitHubRepoResponse.model_validate(r) for r in items],
        total=total,
    )


@router.patch("/repos/{repo_id}/status", response_model=GitHubRepoResponse)
def update_repo_status(
    repo_id: int,
    payload: GitHubRepoStatusUpdate,
    db: DbSession,
    _user: MaintainerUser,
) -> GitHubRepoResponse:
    repo = set_repo_status(db, repo_id, payload.status)
    if repo is None:
        raise HTTPException(status_code=404, detail="仓库不存在")
    return GitHubRepoResponse.model_validate(repo)


@router.get("/reports", response_model=GitHubReportListResponse)
def list_reports(
    db: DbSession,
    _user: CurrentUser,
) -> GitHubReportListResponse:
    items = db.scalars(select(GitHubReport).order_by(GitHubReport.generated_at.desc())).all()
    return GitHubReportListResponse(
        items=[GitHubReportResponse.model_validate(r) for r in items],
        total=len(items),
    )


@router.post("/reports/generate", response_model=GitHubReportResponse)
def generate_report(
    db: DbSession,
    _user: MaintainerUser,
) -> GitHubReportResponse:
    report = generate_daily_report(db)
    return GitHubReportResponse.model_validate(report)


@router.get("/reports/{report_id}", response_model=GitHubReportResponse)
def get_report(
    report_id: int,
    db: DbSession,
    _user: CurrentUser,
) -> GitHubReportResponse:
    report = db.get(GitHubReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="日报不存在")
    return GitHubReportResponse.model_validate(report)