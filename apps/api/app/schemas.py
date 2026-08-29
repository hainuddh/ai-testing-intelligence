from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

Role = Literal["admin", "maintainer", "viewer"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    role: Role


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    source_type: str = Field(min_length=1, max_length=50)
    homepage_url: HttpUrl | None = None
    description: str | None = None
    languages: list[str] = Field(min_length=1)
    trust_level: int = Field(default=3, ge=1, le=5)
    topics: list[str] = Field(default_factory=list)


class SourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    source_type: str
    homepage_url: str | None
    description: str | None
    languages: list[str]
    trust_level: int
    topics: list[str]
    status: str
    health_status: str
    created_by: int
    created_at: datetime
    updated_at: datetime


class SourceListResponse(BaseModel):
    items: list[SourceResponse]
    total: int


class EndpointCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    endpoint_type: Literal["rss", "sitemap", "web", "search", "manual"]
    url: HttpUrl
    fetch_interval_minutes: int = Field(default=360, ge=15, le=10080)
    max_items_per_run: int = Field(default=50, ge=1, le=500)


class EndpointResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    name: str
    endpoint_type: str
    url: str
    fetch_interval_minutes: int
    max_items_per_run: int
    enabled: bool
    health_status: str
    created_at: datetime
