# AI Testing Intelligence Project Handoff

This file stores dynamic cross-session context. Git history and the current
worktree remain the source of truth; verify them before starting work.

## Repository Snapshot (2026-10-11)

- Branch observed: `main`; latest committed product change: `9f5d01e`
  (`feat(web): add reversible source topic management`). Check Git again before acting.
- This snapshot reflects repository implementation, not confirmation that production runs it.
- Current product entry point: [Product overview](product-overview-zh.md); daily email behavior:
  [Daily digest guide](daily-email-digest-zh.md); operation steps: [User manual](user-manual-zh.md).
- Preserve unrelated untracked documentation, scripts, previews, and local skill changes.
- Deployment target: Docker Compose. Previously recorded production constraint: 2 GB RAM with
  swap enabled; recheck actual capacity before changing resource settings.
- This documentation update does not rerun product tests or verify production delivery. Old
  counts of 59 backend tests and 9 frontend tests described an earlier revision and are not
  current validation evidence. Use the relevant test suites and the release's own results.

## Architecture Snapshot

- FastAPI API with synchronous write paths and asynchronous read-heavy paths.
- SQLAlchemy uses separate synchronous and asynchronous engines.
- PostgreSQL stores users, sources, endpoints, collected content, fetch runs, and analysis fields.
- Redis caches content, collection, source, endpoint, and database-status reads; cache failures fall back to PostgreSQL.
- Worker collects feeds/pages, analyzes pending content, and invalidates affected cache prefixes.
- GitHub exploration has persistent scheduling and search cursors, automatically researches
  unreported projects, and publishes at most five projects per Beijing business date. Any
  historical report item permanently excludes that project from future new recommendations.
- `digest_batches.py` persists the daily source-task list, collection window, research progress,
  and preparation/cutoff/send times. Worker completes preparation; `mail_sender.py` independently
  delivers frozen messages without running collection or analysis.
- React/Vite/Ant Design frontend uses request race protection and a short in-memory content cache.
- Source creation/editing/discovery and GitHub preferences share `TopicEditor.tsx`: row editing,
  reversible pending removal, change counts, explicit save, and retained drafts after failure.
- Alembic migrations run through the one-shot Compose `migrate` service before API and Worker startup.

## Current Product Behavior

- GitHub report publication does not require manual discovery, following a repository, or
  generating a report. Failed or analyzed-but-unpublished projects remain eligible for retry.
  Existing reports are reused for the same business date; zero results do not produce an empty
  research report. Old reports and Markdown exports remain readable.
- With email enabled and at least one enabled recipient, GitHub research follows the daily
  preparation schedule. Otherwise successful exploration schedules the next run after 24 hours.
  Failed exploration follows retry and rate-limit waiting rules.
- Daily email combines admitted content intelligence and published GitHub research. The default
  content threshold is strictly above 70; it does not apply again to published GitHub report
  items. Selection and sent-item deduplication are independent for each recipient.
- Preparation starts two hours before the configured Beijing send time; cutoff is ten minutes
  before it. These times are fixed when a batch is created. Changing the send time affects new
  batches, not an existing one. A morning batch remains a morning batch after an afternoon edit.
- Preparation complete is not delivery complete. After preparation and cutoff, a recipient with
  no eligible content from either section gets a skipped, zero-item record; no empty email is
  sent and later arrivals do not reopen that date. Incomplete work delays delivery and is retried.
- New recipients receive content analyzed and GitHub reports generated after their subscription
  start, subject to batch boundaries; historical content is not automatically backfilled.
- Roles: authenticated viewers read/filter/export intelligence and GitHub reports; maintainers
  also create/edit sources, manage endpoints, enter authorized platform content, and manage
  GitHub preferences/status. Deleting a whole source, collected-content administration, user
  management, database status, and all email administration require an administrator.
- Shared topic editing does not merge source topics with GitHub preferences. GitHub topics can
  be cleared to restore language discovery. Manual source creation/editing requires a topic in
  the current UI; automatic source discovery allows topics to be supplied later.
- Current boundaries: GitHub daily research, Markdown export, and daily email are implemented;
  weekly/monthly aggregation, recurring snapshots of already reported projects, IM delivery,
  per-user email subscriptions, and a PAT settings workflow are not. `ATI_GITHUB_TOKEN` remains
  an optional server-side compatibility setting.

## Recent Product Commits

- `9f5d01e`: reversible source topic management, reusing the GitHub editor interaction.
- `271317a`: durable daily batches and independent `mail-sender` delivery.
- `4eb0b24`: recipient management and combined content/GitHub daily digests.
- `5e3f7ed`: daily high-value content email.
- `5394464`: reversible GitHub topic editing.
- `dea3c6b`: automatic exploration and permanent exclusion of previously reported projects.

## Earlier Work and Operational History

These entries preserve previous implementation and deployment notes. They do not establish
the currently deployed revision or the health of the live environment.

- Added deterministic related-link extraction for high-value content. A separate bounded Worker
  stage processes at most three new or existing analyzed items per cycle, fetches each original
  page once, retains at most five real HTTP/HTTPS references, and exposes them in intelligence
  details, collection details, and Markdown reports without another model call or linked-page crawl.
- Added source discovery: the Sources page now has an "自动发现" button; maintainers can enter a website homepage, preview the discovered RSS/Atom feed with article samples, and confirm to atomically create an active source and healthy endpoint. Backend re-validates the feed at install time.
- Added bounded RSS/Atom self-healing: invalid or HTML endpoint responses use feed autodiscovery
  and a small same-site candidate set, broken feed URLs can recover through the configured source
  homepage, and only candidates with valid entries replace the endpoint URL. Namespace-aware
  summary extraction, same-URL refresh, and capped article metadata enrichment repair missing or
  low-quality feed summaries without unbounded crawling.
- Fixed duplicate syndicated content with different URLs bypassing URL-hash deduplication. RSS
  collection and manual platform intake now also compare normalized titles, backed by a
  functional PostgreSQL/SQLite index; Web endpoints remain URL-based so recurring page titles
  do not suppress distinct monitored pages.
- Added first-stage WeChat Official Account and Weibo ingestion: dedicated source categories,
  authorized RSS/Atom endpoints, and maintainer/admin manual URL-title-summary submission that
  never requests platform pages. Manual submissions reuse URL deduplication and the existing
  pending analysis pipeline; platform sources reject Web endpoints.
- Fixed the custom HTTPS proxy to relay both directions continuously, so browser keep-alive requests such as `/auth/me` are no longer held behind the previous response's 30-second read timeout.
- Added `scripts/migrate-to-nginx.sh` for a guarded, low-memory Nginx migration with apt/dnf/yum and Nginx layout detection, DNF/YUM exclude fallback, SELinux-aware ACME Webroot access, local/public HTTP challenge preflight, automatic rollback, and old/new Certbot compatibility. `scripts/ops.sh` now manages Nginx after detecting the migrated site.
- Production migrated to low-memory host Nginx. Because the unfiled mainland-hosted domain receives HTTP-01 `403`, certificate issuance moved to `acme.sh + AliDNS DNS-01`; basic issuance and deployment checks passed without storing credentials in the repository.
- `e64c939`: improved user-creation conflict diagnostics and frontend error-detail display.
- `c2a5fc8`: pinned Python/Node dependencies and added Docker pip/npm cache mounts and mirrors.
- `34d56f1`: added async read paths, Redis caching, low-memory connection pools, and container limits.
- `0563407`: optimized content and collected-content queries with single-scan window queries and indexes.
- `60672a3`: added the initial content-filter composite index.
- `b3cea75`: added collected-content management and Markdown report export.

## Operational Notes

- `docker compose up -d --build` rebuilds images and automatically runs Alembic migrations through `migrate`.
- For an email-capable release, deploy API, Worker, `mail-sender`, and Web together after the
  migration service succeeds. A restart alone does not build changed code or frontend assets.
  See the deployment guide for the release procedure and SMTP environment configuration.
- After applying code and migrations, restart affected processes if only configuration or cache
  settings changed; API and Worker must agree on cache behavior:

  ```bash
  docker compose restart api worker
  ```

- After Nginx configuration or certificate changes, validate and reload the host proxy:

  ```bash
  sudo /usr/sbin/nginx -t && sudo systemctl reload nginx
  ```

- Redis and pip/npm registries are configurable through environment/build arguments documented in `docs/deployment-zh.md`.
- Local backend commands use the project virtual environment with uv; Node commands use pnpm.
- Before claiming a release is deployed, verify the server Git commit, the Web container's
  referenced asset hashes, and the assets actually loaded by the browser. A static-only
  deployment cannot execute backend collection, analysis, or email delivery.
- Do not run `docker compose down -v` when data must be preserved.
- HTTPS proxy, certificate renewal, disk pressure, and production-specific risks are tracked in `docs/go-live-operations-zh.md`.
- Potential inventions follow `docs/invention-governance-zh.md`. Candidate details and unpublished
  evidence are maintained in the Git-ignored `.invention-record.md`; review it for non-trivial
  technical designs and before public disclosure.

## Active Work

- Product documentation refresh is complete. Local Markdown links,
  heading anchors, and code fences were checked across ten updated documents. No product
  behavior or production configuration change is implied by this documentation update.

## Known Follow-ups

- After the first natural acme.sh renewal, verify AliDNS TXT cleanup, installed and live certificate serial equality, and successful Nginx reload.
- Confirm the production user-creation conflict now displays the backend detail instead of only `请求失败 (409)`.
- Add centralized metrics/alerts for container memory, swap, disk, PostgreSQL latency, and Redis availability.
- Periodically review pinned Python/Node versions and update lock metadata intentionally rather than during ordinary builds.
- Evaluate the WeChat official publishing/third-party authorization APIs for owned or explicitly
  authorized accounts, and Weibo commercial data APIs before implementing automated native
  platform collection. Do not add cookie-based scraping or anti-bot bypasses.

## New Session Prompt

Use the following prompt in a new OpenCode session:

```text
请读取 AGENTS.md、docs/ai-handoff.md 和 docs/product-overview-zh.md，检查 git status 和最近 10 个提交，以当前源码核实涉及的行为，再处理以下需求：
<新需求>
```

## Maintenance Rules

- Keep this document concise and current after substantial changes.
- Do not store secrets, `.env` values, tokens, credentials, or private model endpoints here.
- Update verification counts only after running the checks.
- Update the latest verified commit only after a commit has been created.
