from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Momentum:
    score: float
    tier: str


_TIERS = ("fast_mover", "new_notable", "rising", "watch")


def _activity_score(pushed_at: datetime | None, now: datetime) -> float:
    if pushed_at is None:
        return 0.0
    if pushed_at.tzinfo is None:
        pushed_at = pushed_at.replace(tzinfo=UTC)
    age_days = (now - pushed_at).total_seconds() / 86400
    if age_days <= 7:
        return 20.0
    if age_days <= 30:
        return 10.0
    return 0.0


def _size_bonus(stars: int) -> float:
    if stars < 5000:
        return 10.0
    if stars < 20000:
        return 5.0
    return 0.0


def _tier(stars: int, star_delta_24h: int, star_delta_7d: int) -> str:
    if star_delta_7d >= 200:
        return "fast_mover"
    if stars < 5000 and star_delta_7d >= 20:
        return "new_notable"
    if star_delta_7d >= 20 and star_delta_24h > star_delta_7d / 7:
        return "rising"
    return "watch"


def momentum_score(
    stars: int,
    star_delta_24h: int,
    star_delta_7d: int,
    pushed_at: datetime | None,
    now: datetime | None = None,
) -> Momentum:
    """确定性动量打分，不依赖 LLM。

    分数 = 40*7d增量 + 30*24h增量 + 20*活跃度 + 10*规模加成，封顶 100。
    """
    if now is None:
        now = datetime.now(UTC)
    score = (
        40.0 * min(1.0, star_delta_7d / 300.0)
        + 30.0 * min(1.0, star_delta_24h / 60.0)
        + _activity_score(pushed_at, now)
        + _size_bonus(stars)
    )
    score = round(min(100.0, max(0.0, score)), 1)
    return Momentum(score=score, tier=_tier(stars, star_delta_24h, star_delta_7d))