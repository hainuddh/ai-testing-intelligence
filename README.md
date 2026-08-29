# AI Testing Intelligence

AI intelligent testing content discovery, source management, lossless extraction, historical deduplication, and Markdown delivery.

## Development

Backend commands are run from `apps/api`:

```bash
python -m pip install -e ".[dev]"
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
