---
name: ai-testing-intelligence
description: Use when working in the AI Testing Intelligence repository on FastAPI, React, PostgreSQL, Redis, collection workers, testing-intelligence feeds, user or source management, performance, Docker builds, deployment, or operations.
---

# AI Testing Intelligence

Use this project skill to recover context before changing the AI Testing
Intelligence application.

## Context Recovery

Before making changes:

1. Read `README.md`.
2. Read `docs/ai-handoff.md` for the latest state, recent work, and pending tasks.
3. Read `docs/deployment-zh.md` for installation, migration, cache, backup, and upgrade procedures.
4. Read `docs/go-live-operations-zh.md` for the current server topology and operational risks when the request affects production.
5. Inspect `git status --short` and `git log --oneline -10`.
6. Inspect relevant recent commits or diffs before modifying behavior introduced by them.

Do not assume the handoff commit is still current. Git is the source of truth.
Do not revert unrelated changes in a dirty worktree.

## Architecture

- Backend: FastAPI, SQLAlchemy, PostgreSQL, Alembic.
- Frontend: React, Vite, Ant Design.
- Cache: Redis with graceful database fallback.
- Collection and analysis: separate synchronous Worker process.
- Deployment: Docker Compose behind an HTTPS reverse proxy.
- Production constraint: 2 GB RAM with swap enabled.

## Behavioral Boundaries

- Only administrators may access or delete collected content.
- Source deletion is administrator-only because it cascades collected content.
- Maintainers may create and edit sources and manage collection endpoints.
- Intelligence feed items must be analyzed, testing-relevant, and above the configured value threshold.
- Database schema and index changes require an Alembic migration.
- PostgreSQL, Redis, MinIO, and API ports must not be exposed publicly.
- Never commit `.env`, passwords, tokens, model keys, or generated production data.

## Performance Constraints

- Preserve asynchronous read endpoints and Redis cache fallback behavior.
- Preserve small PostgreSQL connection pools and Docker memory limits suitable for the 2 GB server.
- Do not load `ContentItem.body` in list or detail endpoints unless the response explicitly needs it.
- Invalidate relevant Redis prefixes after collection, analysis, deletion, or source changes.
- Keep Python and Node dependencies pinned and keep Docker dependency cache mounts intact.

## Verification

Run checks relevant to the change. For broad changes, run all of them.

Backend from `apps/api`:

```powershell
& "..\..\.venv\Scripts\python.exe" -m pytest tests -q
& "..\..\.venv\Scripts\python.exe" -m ruff check app tests
```

Frontend from `apps/web` on this Windows workspace:

```powershell
cmd /c "npm test -- --run"
cmd /c "npx tsc --noEmit"
```

Compose validation requires the production secrets or temporary non-secret
placeholder environment variables:

```bash
docker compose config --quiet
```

## Handoff Maintenance

After substantial feature, architecture, dependency, migration, performance,
or deployment changes, update `docs/ai-handoff.md` in the same change:

- update the latest verified commit only after the commit exists;
- record verification results and known warnings;
- move finished work out of Active Work;
- add concrete follow-ups and operational risks;
- keep secrets and ephemeral debugging details out of the document.

Do not commit or push unless the user explicitly requests it.
