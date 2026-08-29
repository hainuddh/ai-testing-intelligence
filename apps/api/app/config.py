from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Testing Intelligence"
    database_url: str = "sqlite:///./development.db"
    jwt_secret: str = "development-only-change-me-at-least-32-bytes"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    worker_poll_seconds: int = 60
    fetch_timeout_seconds: int = 20
    fetch_max_bytes: int = 5_000_000

    model_config = SettingsConfigDict(env_file=".env", env_prefix="ATI_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
