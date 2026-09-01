# AI Testing Intelligence Project Handoff

This file stores dynamic cross-session context. Git history and the current
worktree remain the source of truth; verify them before starting work.

## Current State

- Branch: `main`
- Latest verified commit: `e64c939` (`Fix 409 conflict on user creation: pre-check, detailed error message, frontend detail display`)
- Working tree at handoff creation: clean
- Deployment: Docker Compose
- Production resource constraint: 2 GB RAM with swap enabled
- Backend verification: 41 tests passed; Ruff passed
- Frontend verification: 7 tests passed; TypeScript check passed
- Compose configuration: validated with non-secret placeholder environment variables

## Architecture Snapshot

- FastAPI API with synchronous write paths and asynchronous read-heavy paths.
- SQLAlchemy uses separate synchronous and asynchronous engines.
- PostgreSQL stores users, sources, endpoints, collected content, fetch runs, and analysis fields.
- Redis caches content, collection, source, endpoint, and database-status reads; cache failures fall back to PostgreSQL.
- Worker collects feeds/pages, analyzes pending content, and invalidates affected cache prefixes.
- React/Vite/Ant Design frontend uses request race protection and a short in-memory content cache.
- Alembic migrations run through the one-shot Compose `migrate` service before API and Worker startup.

## Important Recent Work

- Fixed the custom HTTPS proxy to relay both directions continuously, so browser keep-alive requests such as `/auth/me` are no longer held behind the previous response's 30-second read timeout.
- Added `scripts/migrate-to-nginx.sh` for a guarded, low-memory Nginx migration with apt/dnf/yum and Nginx layout detection, configuration backup, health verification, automatic rollback, legacy-proxy disablement, and optional Certbot webroot reconfiguration. `scripts/ops.sh` now manages Nginx after detecting the migrated site.
- `e64c939`: improved user-creation conflict diagnostics and frontend error-detail display.
- `c2a5fc8`: pinned Python/Node dependencies and added Docker pip/npm cache mounts and mirrors.
- `34d56f1`: added async read paths, Redis caching, low-memory connection pools, and container limits.
- `0563407`: optimized content and collected-content queries with single-scan window queries and indexes.
- `60672a3`: added the initial content-filter composite index.
- `b3cea75`: added collected-content management and Markdown report export.

## Operational Notes

- `docker compose up -d --build` rebuilds images and automatically runs Alembic migrations through `migrate`.
- After upgrades that change cache behavior, restart API and Worker so cache behavior and code are aligned:

  ```bash
  docker compose restart api worker
  ```

- After deploying the HTTPS proxy keep-alive fix, restart the host proxy service:

  ```bash
  systemctl --user restart ai-testing-intelligence-proxy.service
  ```

- Redis and pip/npm registries are configurable through environment/build arguments documented in `docs/deployment-zh.md`.
- Do not run `docker compose down -v` when data must be preserved.
- HTTPS proxy, certificate renewal, disk pressure, and production-specific risks are tracked in `docs/go-live-operations-zh.md`.

## Active Work

- None at handoff creation.

## Known Follow-ups

- Deploy the Nginx migration documented in `docs/nginx-migration-zh.md`, then verify login reaches `/api/v1/auth/me` without repeated 30-second delays. The Python proxy fix remains only as a rollback improvement.
- Confirm the production user-creation conflict now displays the backend detail instead of only `请求失败 (409)`.
- Resolve the documented Certbot renewal failure before certificate expiry.
- Add centralized metrics/alerts for container memory, swap, disk, PostgreSQL latency, and Redis availability.
- Periodically review pinned Python/Node versions and update lock metadata intentionally rather than during ordinary builds.

## New Session Prompt

Use the following prompt in a new OpenCode session:

```text
请加载 ai-testing-intelligence skill，读取 docs/ai-handoff.md，检查 git status 和最近 10 个提交，然后直接处理以下需求：
<新需求>
```

## Maintenance Rules

- Keep this document concise and current after substantial changes.
- Do not store secrets, `.env` values, tokens, credentials, or private model endpoints here.
- Update verification counts only after running the checks.
- Update the latest verified commit only after a commit has been created.
