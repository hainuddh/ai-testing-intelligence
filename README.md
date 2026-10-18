# AI Testing Intelligence

SIGNAL ATLAS turns technical content and GitHub projects into actionable testing intelligence. Users define focus areas; the platform discovers and trials sources, collects approved feeds, analyzes content, researches previously unreported projects, and delivers Markdown reports and daily email digests.

## Product documentation

Current product scope and behavior, reviewed on 2026-10-18:

- [产品说明 / Product overview](docs/product-overview-zh.md): capabilities, roles, workflows, topic management, delivery rules, and current limits.
- [用户操作手册 / User manual](docs/user-manual-zh.md): navigation and daily operations.
- [关注方向与信源工作台 / Source workspace](docs/source-management-zh.md): automatic discovery, isolated trials, human approval, contribution evidence, pause and archive.
- [每日情报邮件 / Daily email](docs/daily-email-digest-zh.md): recipient windows, eligibility, schedules, statuses, SMTP, and troubleshooting.
- [GitHub automatic discovery](docs/github-auto-discovery-plan.md): current discovery and historical-report exclusion rules; older sections are explicitly historical.

Source exploration runs automatically within a maintained catalog and real reference sites found in analyzed content. Trials are isolated from formal collection and email; a maintainer or administrator must confirm promotion. Source contribution and health evidence support pause, restore, and archive decisions without deleting history. GitHub research recommends at most five previously unreported projects per business day.

Source and GitHub topics use explicit add/edit/remove drafts with undo before saving. Email combines eligible content and GitHub reports for each recipient. Existing daily batches retain their original schedule after a time setting changes; empty digests are skipped. Preparation completion and email delivery are separate states.

## Development

Backend commands are run from `apps/api`:

```bash
uv venv
uv pip install --python .venv/bin/python -e ".[dev]" --index-url https://mirrors.aliyun.com/pypi/simple/
uv run --no-project .venv/bin/alembic upgrade head
uv run --no-project .venv/bin/pytest
uv run --no-project .venv/bin/uvicorn app.main:app --reload
```

These commands use a project-local virtual environment. On Windows, use `.venv\Scripts\python.exe`, `.venv\Scripts\alembic.exe`, `.venv\Scripts\pytest.exe`, and `.venv\Scripts\uvicorn.exe` for the corresponding paths.

Run the background processes in separate terminals from `apps/api`:

```bash
uv run --no-project .venv/bin/python -m app.worker
uv run --no-project .venv/bin/python -m app.mail_sender
```

Set the local backend environment in `apps/api/.env` for all three processes; the root `.env` is used by Docker Compose. See the email guide for SMTP setup. The API alone does not run scheduled collection or email delivery.

The web application is under `apps/web`:

```bash
pnpm install
pnpm exec vitest run
pnpm dev
```

For end-user instructions covering roles, intelligence search and export, source management,
manual content submission, and administrator functions, see the
[Chinese user manual](docs/user-manual-zh.md).

## Docker

For production installation, HTTPS, backup, recovery, upgrade, and troubleshooting
instructions, see the [Chinese deployment guide](docs/deployment-zh.md). For the current
server's actual domain migration, service layout, verification commands, and known risks,
see the [Chinese go-live operations record](docs/go-live-operations-zh.md).

Common maintenance commands:

```bash
./scripts/ops.sh status
./scripts/ops.sh health
./scripts/ops.sh restart
./scripts/ops.sh upgrade main
```

Create `.env` from `.env.example`, replace all secrets, then start the stack:

```bash
docker compose up --build
```

The management UI is available at `http://localhost:8080`. The stack includes API, collection/research Worker, independent `mail-sender`, Web, PostgreSQL, Redis, MinIO, and a one-shot migration service. PostgreSQL, Redis, and MinIO
are only available to containers on the internal Compose network.

Create the first administrator after the API is running:

```bash
docker compose exec api python -m app.bootstrap admin 'replace-this-password'
```

Define platform focus areas in Source Management to start automatic discovery and isolated
trials, then review samples and confirm sources for formal collection. You can also add a
website by URL or configure RSS/Atom or Web endpoints manually. The `worker` service
collects due endpoints of active sources and analyzes content for the intelligence feed.

WeChat Official Accounts and Weibo are available as dedicated source categories. For these
platforms, configure only an authorized, traceable RSS/Atom feed, or use **Manual Content**
to submit an original URL, title, and summary. Manual submission does not request the
platform page; it stores the supplied evidence and sends it through the existing deduplication
and testing-intelligence analysis pipeline. Maintainers and administrators can submit content.

Add a small, idempotent set of real AI news feeds after creating the administrator:

```bash
docker compose exec api python -m app.seed_sources --username admin
docker compose restart worker
docker compose logs -f worker
```

The command only creates missing sample sources and endpoints. It does not insert fake
articles; the worker collects current entries from the configured publishers.

Reset only the built-in sample sources, their endpoints, fetch history, and collected content:

```bash
docker compose exec api python -m app.seed_sources --username admin --reset
docker compose restart worker
```

Configure an OpenAI-compatible analysis model in `.env` to turn collected articles into
testing intelligence:

```dotenv
ATI_ANALYSIS_API_BASE_URL=https://api.openai.com/v1
ATI_ANALYSIS_API_KEY=replace-with-your-model-api-key
ATI_ANALYSIS_MODEL=gpt-4o-mini
```

The worker evaluates testing relevance and value, filters general AI news out of the main
radar, and generates the in-app summary, testing scenarios, adoption suggestions, and risks.
Without a configured model, collected articles remain pending and the intelligence feed is
intentionally empty rather than presenting unreviewed general AI news.

Only HTTPS analysis endpoints are accepted. Analysis uses the stored body or summary and
can fetch article text adaptively when evidence is short or the initial result needs more
context. Adaptive retrieval is enabled by default; WeChat and Weibo sources are excluded
from this page-fetching path. `ATI_ANALYSIS_FETCH_FULL_CONTENT=true` enables broader article
prefetching. See the deployment guide for these settings and the separate related-link stage.

Select one or more analyzed intelligence cards in the web interface and use **Export
Markdown** to download a merged testing report. Reports preserve the selection order and
include scores, testing value analysis, applicable scenarios, adoption suggestions, risks,
tags, and original evidence links.

Administrators can use **Collected Content** to query every stored item, including pending,
analyzed, filtered, and failed records. The view supports keyword, source, status, and date
filters plus guarded single or bulk deletion. Deleting collected content also removes its
analysis result, but does not delete the source or collection endpoint.

Source deletion is admin-only because it cascades collected content. Maintainers can still
create and edit sources and manage collection endpoints.

## Performance & Caching

The API uses an async read path (`async def` + `AsyncSession`) and caches list, detail, and
statistics responses in Redis to reduce database load on memory-constrained hosts.

- List queries for the intelligence feed, collected content, sources, and the database
  status page are cached in Redis with short TTLs (default 30–60s).
- The worker invalidates feed caches immediately after fetching or analyzing new content.
- Cache writes for the read path are non-blocking; if Redis is unreachable the API silently
  falls back to direct queries.
- List queries defer loading the large `body` column to reduce per-request memory.
- `compose.yaml` ships tuned PostgreSQL settings, Redis `maxmemory`, per-container memory
  limits, and a small database connection pool suitable for a 2 GB server.

Set `ATI_REDIS_URL` in `.env` to enable caching (default `redis://redis:6379/0` in Compose).
Leaving it empty disables caching without affecting functionality. See
[docs/deployment-zh.md](docs/deployment-zh.md) for the full environment reference.
