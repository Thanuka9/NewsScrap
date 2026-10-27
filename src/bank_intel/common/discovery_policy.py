from __future__ import annotations

from datetime import date
from urllib.parse import urlparse
import re

class OldPageStopper:
    """Stop only after two consecutive, fully dated, out-of-window pages.

    One old article on a mixed page does NOT indicate that every subsequent
    page is old. A missing date, mixed dates, or a page fetch error resets
    the streak. max_pages remains an absolute safety ceiling.
    """
    def __init__(self, start_date: date, *, consecutive_pages: int = 2,
                 minimum_dates: int = 3):
        self.start_date = start_date
        self.required = consecutive_pages
        self.minimum_dates = minimum_dates
        self.streak = 0

    def observe(self, dates: list[date], *, unknown_dates: int = 0,
                failed: bool = False) -> bool:
        known = [d for d in dates if d is not None]
        fully_older = (not failed and not unknown_dates
                       and len(known) >= self.minimum_dates
                       and all(d < self.start_date for d in known))
        self.streak = self.streak + 1 if fully_older else 0
        return self.streak >= self.required


def publication_date_in_url(url: str) -> date | None:
    match = re.search(r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)",
                      urlparse(url).path)
    if not match:
        return None
    try:
        return date(*(int(group) for group in match.groups()))
    except ValueError:
        return None
