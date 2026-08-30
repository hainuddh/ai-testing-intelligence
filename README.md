# AI Testing Intelligence

AI intelligent testing content discovery, source management, lossless extraction, historical deduplication, and Markdown delivery.

## Development

Backend commands are run from `apps/api`:

```bash
python -m pip install -e ".[dev]"
alembic upgrade head
pytest
uvicorn app.main:app --reload
```

The web application is under `apps/web`:

```bash
npm install
npm test -- --run
npm run dev
```

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

The management UI is available at `http://localhost:8080`. PostgreSQL, Redis, and MinIO
are only available to containers on the internal Compose network.

Create the first administrator after the API is running:

```bash
docker compose exec api python -m app.bootstrap admin 'replace-this-password'
```

Add RSS/Atom or Web collection endpoints from Source Management. The `worker` service
collects due endpoints and makes discovered content available in the intelligence feed.

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

Only HTTPS analysis endpoints are accepted. By default, analysis sends the feed title and
summary, not a separately downloaded full article. Set `ATI_ANALYSIS_FETCH_FULL_CONTENT=true`
only after reviewing the model provider's data retention, copyright, and cost implications.
