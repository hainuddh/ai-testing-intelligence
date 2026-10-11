"""Calendar-based Beijing schedules shared by preparation and delivery."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone

SHANGHAI = timezone(timedelta(hours=8))
PREPARATION_LEAD = timedelta(hours=2)
CUTOFF_LEAD = timedelta(minutes=10)


@dataclass(frozen=True)
class DigestSchedule:
    business_date: date
    send_at: datetime

    @property
    def prepare_at(self) -> datetime:
        return self.send_at - PREPARATION_LEAD

    @property
    def cutoff_at(self) -> datetime:
        return self.send_at - CUTOFF_LEAD


def schedule_for_day(send_time: str, day: date) -> DigestSchedule:
    hour, minute = map(int, send_time.split(":"))
    send_at = datetime.combine(day, datetime.min.time(), tzinfo=SHANGHAI).replace(
        hour=hour, minute=minute
    )
    return DigestSchedule(day, send_at.astimezone(UTC))


def digest_schedule(send_time: str, now: datetime) -> DigestSchedule:
    now = now.replace(tzinfo=now.tzinfo or UTC).astimezone(UTC)
    day = now.astimezone(SHANGHAI).date()
    tomorrow = schedule_for_day(send_time, day + timedelta(days=1))
    # 00:00–01:59 mail starts preparation on the previous evening.
    return tomorrow if now >= tomorrow.prepare_at else schedule_for_day(send_time, day)
