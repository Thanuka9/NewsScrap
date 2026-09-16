from datetime import datetime
from zoneinfo import ZoneInfo

from bank_intel.common.collection_window import (
    get_collection_window,
    publication_dates_for_window,
)

TZ = ZoneInfo("Asia/Colombo")


def dates_for(run_dt):
    return publication_dates_for_window(get_collection_window(run_dt))


def test_tuesday_runs_yesterday_and_today():
    run_dt = datetime(2026, 9, 15, 8, 0, tzinfo=TZ)
    assert dates_for(run_dt) == [
        "2026-09-14",
        "2026-09-15",
    ]


def test_monday_runs_friday_through_monday():
    run_dt = datetime(2026, 9, 14, 8, 0, tzinfo=TZ)
    assert dates_for(run_dt) == [
        "2026-09-11",
        "2026-09-12",
        "2026-09-13",
        "2026-09-14",
    ]
