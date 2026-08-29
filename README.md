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
