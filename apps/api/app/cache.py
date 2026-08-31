import json
import logging
from hashlib import sha256
from typing import Any

from redis import Redis as SyncRedis
from redis.asyncio import Redis as AsyncRedis
from redis.exceptions import RedisError

from app.config import settings

logger = logging.getLogger(__name__)

_async_client: AsyncRedis | None = None


def _get_async() -> AsyncRedis | None:
    global _async_client
    if _async_client is None:
        if not settings.redis_url:
            return None
        _async_client = AsyncRedis.from_url(
            settings.redis_url,
            socket_connect_timeout=1.0,
            socket_timeout=1.0,
            max_connections=4,
            decode_responses=True,
        )
    return _async_client


def _get_sync() -> SyncRedis | None:
    if not settings.redis_url:
        return None
    return SyncRedis.from_url(
        settings.redis_url,
        socket_connect_timeout=1.0,
        socket_timeout=1.0,
        max_connections=2,
        decode_responses=True,
    )


def cache_key(prefix: str, **parts) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str)
    return f"{prefix}:{sha256(raw.encode()).hexdigest()[:24]}"


# --- Async helpers (used by async API endpoints) ---

async def get_json(key: str) -> Any | None:
    client = _get_async()
    if client is None:
        return None
    try:
        raw = await client.get(key)
    except (RedisError, TimeoutError):
        logger.debug("Redis GET failed for %s", key)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


async def set_json(key: str, value: Any, ttl: int) -> None:
    client = _get_async()
    if client is None:
        return
    try:
        await client.setex(key, ttl, json.dumps(value, ensure_ascii=False, default=str))
    except (RedisError, TimeoutError):
        logger.debug("Redis SET failed for %s", key)


async def delete_prefix(prefix: str) -> None:
    client = _get_async()
    if client is None:
        return
    try:
        async for key in client.scan_iter(match=f"{prefix}:*", count=200):
            await client.delete(key)
    except (RedisError, TimeoutError):
        logger.debug("Redis SCAN/DELETE failed for %s", prefix)


# --- Sync helpers (used by sync endpoints, worker, CLI) ---

def delete_prefix_sync(prefix: str) -> None:
    client = _get_sync()
    if client is None:
        return
    try:
        try:
            cursor = 0
            while True:
                cursor, keys = client.scan(cursor=cursor, match=f"{prefix}:*", count=200)
                if keys:
                    client.delete(*keys)
                if cursor == 0:
                    break
        finally:
            client.close()
    except (RedisError, TimeoutError):
        logger.debug("Sync SCAN/DELETE failed for %s", prefix)