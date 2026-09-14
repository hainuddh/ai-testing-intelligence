from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import GitHubRepo, GitHubReport, GitHubReportItem, GitHubSnapshot


def test_create_repo_defaults(db_session):
    repo = GitHubRepo(full_name="test/repo", html_url="https://github.com/test/repo")
    db_session.add(repo)
    db_session.commit()
    assert repo.id is not None
    assert repo.status == "discovered"
    assert repo.summary_status == "none"
    assert repo.archived is False


def test_repo_full_name_unique(db_session):
    db_session.add(GitHubRepo(full_name="dup/repo", html_url="https://github.com/dup/repo"))
    db_session.commit()
    db_session.add(GitHubRepo(full_name="dup/repo", html_url="https://github.com/dup/repo"))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_snapshot_links_to_repo(db_session):
    repo = GitHubRepo(full_name="test/repo", html_url="https://github.com/test/repo")
    db_session.add(repo)
    db_session.flush()
    db_session.add(GitHubSnapshot(repo_id=repo.id, stars=100, forks=10))
    db_session.commit()
    assert repo.snapshots[0].stars == 100


def test_snapshot_repo_snapshot_at_unique(db_session):
    repo = GitHubRepo(full_name="test/repo", html_url="https://github.com/test/repo")
    db_session.add(repo)
    db_session.flush()
    at = datetime.now(UTC)
    db_session.add(GitHubSnapshot(repo_id=repo.id, snapshot_at=at, stars=1))
    db_session.add(GitHubSnapshot(repo_id=repo.id, snapshot_at=at, stars=2))
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_report_and_item(db_session):
    repo = GitHubRepo(full_name="test/repo", html_url="https://github.com/test/repo")
    db_session.add(repo)
    db_session.flush()
    report = GitHubReport(report_type="daily", title="日报")
    db_session.add(report)
    db_session.flush()
    db_session.add(GitHubReportItem(report_id=report.id, repo_id=repo.id, rank=1))
    db_session.commit()
    assert report.items[0].rank == 1