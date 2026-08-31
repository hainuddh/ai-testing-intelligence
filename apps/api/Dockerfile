# syntax=docker/dockerfile:1
FROM python:3.12-slim

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_INDEX_URL=${PIP_INDEX_URL}

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations

RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-cache-dir .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]