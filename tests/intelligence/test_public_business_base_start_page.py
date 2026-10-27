from datetime import date

from bank_intel.collectors.sources._public_business_base import (
    BusinessSourceAdapter,
    FetchResult,
    run_collection,
)


class EmptyListingAdapter(
    BusinessSourceAdapter
):
    source_key = "test_resume"
    source_name = "Test Resume"
    section_name = "Business"
    section_url = "https://example.com/business"
    allow_browser_fallback = False

    def __init__(self):
        self.pages = []

    def listing_url(
        self,
        page_number: int,
    ) -> str:
        self.pages.append(
            page_number
        )
        return (
            "https://example.com/"
            f"page/{page_number}"
        )

    def fetch(
        self,
        url: str,
    ) -> FetchResult:
        return FetchResult(
            requested_url=url,
            final_url=url,
            html="<html>" + ("x" * 600) + "</html>",
            status_code=200,
            method="test",
        )

    def discover_article_urls(
        self,
        html: str,
        base_url: str,
    ) -> list[str]:
        return []

    def is_article_url(
        self,
        url: str,
    ) -> bool:
        return False


def test_default_collection_starts_at_page_one():
    adapter = EmptyListingAdapter()

    status = run_collection(
        adapter,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 10, 7),
        max_pages=5,
        sleep_seconds=0,
    )

    assert adapter.pages == [
        1,
        2,
    ]
    assert status[
        "start_page"
    ] == 1
    assert status[
        "last_page_attempted"
    ] == 2


def test_resumed_collection_starts_at_requested_page():
    adapter = EmptyListingAdapter()

    status = run_collection(
        adapter,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 10, 7),
        max_pages=5,
        sleep_seconds=0,
        start_page=26,
    )

    assert adapter.pages == [
        26,
        27,
    ]
    assert status[
        "start_page"
    ] == 26
    assert status[
        "last_page_attempted"
    ] == 27
