from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo


# ============================================================
# TIMEZONE
# ============================================================

SRI_LANKA_TZ = ZoneInfo(
    "Asia/Colombo"
)


# ============================================================
# COLLECTION WINDOW
# ============================================================


@dataclass(frozen=True)
class CollectionWindow:
    """
    Publication-date window inspected during one collection run.

    Important distinction:

        run_date
            Date on which our system is executing.

        start_date / end_date
            Publication dates that the collectors are allowed
            to inspect during this run.

    This deliberately includes a small lookback so late-posted
    or late-discovered articles are not lost.
    """

    start_date: date
    end_date: date

    run_datetime: datetime

    mode: str

    @property
    def run_date(self) -> date:
        return self.run_datetime.date()

    def contains_date(
        self,
        value: date,
    ) -> bool:

        return (
            self.start_date
            <= value
            <= self.end_date
        )

    def is_late_publication_date(
        self,
        value: date,
    ) -> bool:
        """
        True when the publication belongs to an earlier calendar
        date than the current collection run.

        This does NOT automatically mean that the newspaper
        itself published it late.

        It means our system first processes it on a later
        collection date.
        """

        return value < self.run_date


# ============================================================
# WINDOW CREATION
# ============================================================


def get_collection_window(
    run_datetime: Optional[
        datetime
    ] = None,
) -> CollectionWindow:
    """
    Build the operational discovery window.

    Production policy
    -----------------

    Monday:
        Friday -> Monday

        This covers:
            Friday evening articles published after our
            final Friday collection,
            Saturday,
            Sunday,
            Monday.

    Tuesday-Friday:
        Yesterday -> Today

        This catches articles appearing after the previous
        day's final scheduled collection.

    Saturday/Sunday:
        Current day only.

        Weekend execution is expected to be manual because
        the production scheduler runs Monday-Friday.
        Monday will still recover weekend material.

    Examples
    --------

    Monday 2026-09-14:
        2026-09-11 -> 2026-09-14

    Tuesday 2026-09-15:
        2026-09-14 -> 2026-09-15
    """

    if run_datetime is None:

        run_datetime = datetime.now(
            SRI_LANKA_TZ
        )

    elif run_datetime.tzinfo is None:

        run_datetime = (
            run_datetime.replace(
                tzinfo=SRI_LANKA_TZ
            )
        )

    else:

        run_datetime = (
            run_datetime.astimezone(
                SRI_LANKA_TZ
            )
        )

    run_date = (
        run_datetime.date()
    )

    weekday = (
        run_date.weekday()
    )

    # Python:
    #
    # Monday    = 0
    # Tuesday   = 1
    # Wednesday = 2
    # Thursday  = 3
    # Friday    = 4
    # Saturday  = 5
    # Sunday    = 6

    # ========================================================
    # MONDAY
    #
    # Friday -> Monday
    # ========================================================

    if weekday == 0:

        start_date = (
            run_date
            - timedelta(
                days=3
            )
        )

        mode = (
            "MONDAY_WEEKEND_CATCHUP"
        )

    # ========================================================
    # TUESDAY-FRIDAY
    #
    # Yesterday -> Today
    # ========================================================

    elif weekday in {
        1,
        2,
        3,
        4,
    }:

        start_date = (
            run_date
            - timedelta(
                days=1
            )
        )

        mode = (
            "DAILY_WITH_LOOKBACK"
        )

    # ========================================================
    # WEEKEND MANUAL RUN
    # ========================================================

    else:

        start_date = (
            run_date
        )

        mode = (
            "WEEKEND_MANUAL"
        )

    # If the previous daily edition is still held, its unreported coverage
    # must not disappear from tomorrow's discovery window. Read-only check.
    from pathlib import Path
    from bank_intel.publication.backlog import earliest_unpublished_day
    report_root = Path(__file__).resolve().parents[3] / "data" / "processed" / "reports"
    backlog_start = earliest_unpublished_day(report_root, run_date)
    if backlog_start < start_date:
        start_date = backlog_start
        mode = "UNSENT_PREVIOUS_EDITION_CATCHUP"

    return CollectionWindow(
        start_date=start_date,
        end_date=run_date,
        run_datetime=run_datetime,
        mode=mode,
    )


# ============================================================
# ARTICLE DATE PARSER
# ============================================================


def parse_article_date(
    published_at: str | None,
) -> date | None:
    """
    Convert normalized ISO publication timestamp into the
    Sri Lankan calendar date.

    Examples:

        2026-09-15T05:31:00+05:30
            -> 2026-09-15

        2026-09-14T23:00:00+00:00
            -> Sri Lanka local date
    """

    if not published_at:
        return None

    try:

        parsed = (
            datetime.fromisoformat(
                published_at
            )
        )

    except (
        TypeError,
        ValueError,
    ):

        return None

    if parsed.tzinfo is None:

        parsed = (
            parsed.replace(
                tzinfo=SRI_LANKA_TZ
            )
        )

    else:

        parsed = (
            parsed.astimezone(
                SRI_LANKA_TZ
            )
        )

    return parsed.date()


# ============================================================
# INTAKE CLASSIFICATION
# ============================================================


def classify_intake_status(
    *,
    published_at: str | None,
    collection_status: str,
    window: CollectionWindow,
) -> str:
    """
    Classify how a newly stored article entered the system.

    collection_status is expected to be:

        NEW
        CHANGED
        UNCHANGED

    Examples:

        Article published today and first collected today:
            NEW_TODAY

        Article published yesterday but first collected today:
            LATE_DISCOVERY

        Existing article changed:
            UPDATED_ARTICLE

        Existing article unchanged:
            EXISTING_ARTICLE
    """

    if collection_status == "CHANGED":

        return "UPDATED_ARTICLE"

    if collection_status == "UNCHANGED":

        return "EXISTING_ARTICLE"

    published_date = (
        parse_article_date(
            published_at
        )
    )

    if published_date is None:

        return (
            "NEW_DATE_UNKNOWN"
        )

    if (
        published_date
        < window.run_date
    ):

        return (
            "LATE_DISCOVERY"
        )

    return "NEW_TODAY"


# ============================================================
# INTELLIGENCE PUBLICATION DATES
# ============================================================


def publication_dates_for_window(
    window: CollectionWindow,
) -> list[str]:
    """
    Return every ACTUAL publication date Stage-1 intelligence
    should inspect after a successful collection run.

    This deliberately uses the same operational lookback window
    as collection.

    Examples
    --------

    Monday 2026-09-14:
        [
            "2026-09-11",
            "2026-09-12",
            "2026-09-13",
            "2026-09-14",
        ]

    Wednesday 2026-09-16:
        [
            "2026-09-15",
            "2026-09-16",
        ]
    """

    values = []

    current = (
        window.start_date
    )

    while (
        current
        <= window.end_date
    ):

        values.append(
            current.isoformat()
        )

        current = (
            current
            + timedelta(
                days=1
            )
        )

    return values
