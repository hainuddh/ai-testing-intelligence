from datetime import UTC, datetime, timedelta

from app.github_momentum import momentum_score


def test_fast_mover_when_seven_day_delta_large():
    m = momentum_score(stars=50000, star_delta_24h=50, star_delta_7d=300, pushed_at=None)
    assert m.tier == "fast_mover"


def test_new_notable_for_small_repo_with_signal():
    m = momentum_score(stars=1000, star_delta_24h=10, star_delta_7d=50, pushed_at=None)
    assert m.tier == "new_notable"


def test_rising_for_large_repo_accelerating():
    # 24h(30) > 7d 日均(100/7≈14.3)，且 star>=5000
    m = momentum_score(stars=8000, star_delta_24h=30, star_delta_7d=100, pushed_at=None)
    assert m.tier == "rising"


def test_watch_by_default():
    m = momentum_score(stars=30000, star_delta_24h=1, star_delta_7d=10, pushed_at=None)
    assert m.tier == "watch"


def test_watch_for_large_repo_not_accelerating():
    # >=5000 star 但 24h(10) <= 7d 日均(14.3)，不加速
    m = momentum_score(stars=8000, star_delta_24h=10, star_delta_7d=100, pushed_at=None)
    assert m.tier == "watch"


def test_score_components_are_deterministic():
    now = datetime(2026, 1, 10, tzinfo=UTC)
    pushed = now - timedelta(days=3)
    # 7d 150 -> 40*0.5=20；24h 30 -> 30*0.5=15；活跃(7天内)=20；规模<5k=10 => 65.0
    m = momentum_score(stars=1000, star_delta_24h=30, star_delta_7d=150, pushed_at=pushed, now=now)
    assert m.score == 65.0


def test_activity_degrades_with_age():
    now = datetime(2026, 1, 10, tzinfo=UTC)
    recent = now - timedelta(days=3)
    old = now - timedelta(days=60)
    m_recent = momentum_score(
        stars=30000, star_delta_24h=0, star_delta_7d=0, pushed_at=recent, now=now
    )
    m_old = momentum_score(stars=30000, star_delta_24h=0, star_delta_7d=0, pushed_at=old, now=now)
    assert m_recent.score == 20.0  # 仅活跃分
    assert m_old.score == 0.0


def test_score_caps_at_100():
    m = momentum_score(
        stars=1000, star_delta_24h=1000, star_delta_7d=1000, pushed_at=datetime.now(UTC)
    )
    assert m.score == 100.0


def test_negative_delta_clamped_to_zero():
    m = momentum_score(stars=30000, star_delta_24h=-5, star_delta_7d=-10, pushed_at=None)
    assert m.score == 0.0
    assert m.tier == "watch"
