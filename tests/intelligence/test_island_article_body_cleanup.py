from bank_intel.collectors.sources.island import (
    IslandBusinessAdapter,
)


def test_island_body_truncates_recommendation_feed():
    contaminated = """
Business

Published

on

By

Share

Tweet

AirAsia has been named the World's Best Low-Cost Airline for the
17th consecutive year at the 2026 Skytrax World Airline Awards.

The airline operates services between Colombo and Kuala Lumpur.

All News Advertisement

Munchee makes SLIM DIGIS history with record breaking 20 awards

Oil prices exceed US$ 100 per barrel; stock trading momentum retarded

Business

Published

on

By

As part of World Investor Week, the Securities and Exchange Commission
and Colombo Stock Exchange rang the opening bell.
"""

    cleaned = (
        IslandBusinessAdapter
        ._clean_island_article_text(
            contaminated
        )
    )

    assert cleaned.startswith(
        "AirAsia has been named"
    )
    assert "All News Advertisement" not in cleaned
    assert "Munchee makes SLIM DIGIS" not in cleaned
    assert "Oil prices exceed" not in cleaned
    assert "World Investor Week" not in cleaned


def test_island_body_keeps_real_byline():
    contaminated = """
Business
Published
on
By
Share
Tweet

By Hiran H. Senewiratne

The CSE yesterday indicated a stable outlook initially but was not able
to sustain its momentum due to uncertainties in the external environment.

All News Advertisement

AirAsia named World's Best Low-Cost Airline for 17th consecutive year
"""

    cleaned = (
        IslandBusinessAdapter
        ._clean_island_article_text(
            contaminated
        )
    )

    assert cleaned.startswith(
        "By Hiran H. Senewiratne"
    )
    assert "The CSE yesterday" in cleaned
    assert "AirAsia named" not in cleaned


def test_island_body_without_tail_is_preserved():
    clean = """
Business
Published
on
By
Share
Tweet

Ceybank celebrates 35 years of stability and growth.

The fund reported another year of steady performance.
"""

    result = (
        IslandBusinessAdapter
        ._clean_island_article_text(
            clean
        )
    )

    assert result.startswith(
        "Ceybank celebrates"
    )
    assert "steady performance" in result
