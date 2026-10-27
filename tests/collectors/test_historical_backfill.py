from dataclasses import (
    dataclass,
)

from datetime import (
    date,
)

from scripts.run_historical_backfill import (
    BackfillSource,
    discover_historical_articles,
)


@dataclass
class FakeItem:

    headline: str

    url: str

    published_at_hint: (
        str
        | None
    )


@dataclass
class FakeResult:

    html: str


class FakeClient:

    def get(
        self,
        url: str,
    ) -> FakeResult:

        return (
            FakeResult(
                html=url
            )
        )


def test_historical_discovery_stops_after_older_boundary():

    page_items = {

        "page-1": [

            FakeItem(
                "newer",
                "https://example.com/newer",
                "2026-09-17T10:00:00+05:30",
            ),

            FakeItem(
                "in range 1",
                "https://example.com/a",
                "2026-09-16T10:00:00+05:30",
            ),
        ],


        "page-2": [

            FakeItem(
                "in range 2",
                "https://example.com/b",
                "2026-09-15T10:00:00+05:30",
            ),

            FakeItem(
                "older",
                "https://example.com/older",
                "2026-09-14T10:00:00+05:30",
            ),
        ],


        "page-3": [

            FakeItem(
                "should not be scanned",
                "https://example.com/c",
                "2026-09-13T10:00:00+05:30",
            ),
        ],
    }


    source = (
        BackfillSource(

            source_key="fake",

            source_name="Fake",

            section_name="Finance",

            build_page_url=(
                lambda page_number:
                f"page-{page_number}"
            ),

            discover_articles=(
                lambda html:
                page_items[
                    html
                ]
            ),
        )
    )


    result = (
        discover_historical_articles(

            client=(
                FakeClient()
            ),

            source=(
                source
            ),

            start_date=(
                date(
                    2026,
                    9,
                    15,
                )
            ),

            end_date=(
                date(
                    2026,
                    9,
                    16,
                )
            ),

            max_pages=10,

            request_delay_seconds=0,
        )
    )


    assert {
        item.url

        for item in (
            result.articles
        )
    } == {

        "https://example.com/a",

        "https://example.com/b",
    }


    assert (
        result.pages_scanned
        == 2
    )


    assert (
        result.reached_older_boundary
        is True
    )


    assert (
        result.max_pages_reached
        is False
    )