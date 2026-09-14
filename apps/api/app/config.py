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
    github_token: str = ""  # 预留：MVP 不接 PAT，非空时客户端自动带 Bearer
    github_discovery_languages: str = "python,typescript,go,rust,javascript"
    analysis_api_base_url: str = ""
    analysis_api_key: str = ""
    analysis_model: str = ""
    analysis_batch_size: int = 10
    testing_relevance_threshold: int = 60
    analysis_fetch_full_content: bool = False
    redis_url: str = ""
    content_cache_ttl: int = 30
    database_cache_ttl: int = 60
    sources_cache_ttl: int = 30
    db_pool_size: int = 3
    db_max_overflow: int = 3

    @property
    def github_discovery_languages_list(self) -> list[str]:
        return [x.strip() for x in self.github_discovery_languages.split(",") if x.strip()]

    model_config = SettingsConfigDict(env_file=".env", env_prefix="ATI_", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
