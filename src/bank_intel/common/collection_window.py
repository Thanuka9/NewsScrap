from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

SRI_LANKA_TZ = ZoneInfo("Asia/Colombo")

@dataclass(frozen=True)
class CollectionWindow:
    start_date: date
    end_date: date
    run_datetime: datetime
    mode: str

    @property
    def run_date(self) -> date:
        return self.run_datetime.date()

    def contains_date(self, value: date) -> bool:
        return self.start_date <= value <= self.end_date

    def is_late_publication_date(self, value: date) -> bool:
        return value < self.run_date


def get_collection_window(run_datetime: Optional[datetime] = None) -> CollectionWindow:
    if run_datetime is None:
        run_datetime = datetime.now(SRI_LANKA_TZ)
    elif run_datetime.tzinfo is None:
        run_datetime = run_datetime.replace(tzinfo=SRI_LANKA_TZ)
    else:
        run_datetime = run_datetime.astimezone(SRI_LANKA_TZ)

    run_date = run_datetime.date()
    weekday = run_date.weekday()
    if weekday == 0:
        start_date = run_date - timedelta(days=3)
        mode = "MONDAY_WEEKEND_CATCHUP"
    elif weekday in {1, 2, 3, 4}:
        start_date = run_date - timedelta(days=1)
        mode = "DAILY_WITH_LOOKBACK"
    else:
        start_date = run_date
        mode = "WEEKEND_MANUAL"

    return CollectionWindow(start_date=start_date, end_date=run_date, run_datetime=run_datetime, mode=mode)


def parse_article_date(published_at: str | None) -> date | None:
    if not published_at:
        return None
    try:
        parsed = datetime.fromisoformat(published_at)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SRI_LANKA_TZ)
    else:
        parsed = parsed.astimezone(SRI_LANKA_TZ)
    return parsed.date()


def classify_intake_status(*, published_at: str | None, collection_status: str, window: CollectionWindow) -> str:
    if collection_status == "CHANGED":
        return "UPDATED_ARTICLE"
    if collection_status == "UNCHANGED":
        return "EXISTING_ARTICLE"
    published_date = parse_article_date(published_at)
    if published_date is None:
        return "NEW_DATE_UNKNOWN"
    if published_date < window.run_date:
        return "LATE_DISCOVERY"
    return "NEW_TODAY"
