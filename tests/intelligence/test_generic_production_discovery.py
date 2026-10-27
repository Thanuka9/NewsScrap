
from datetime import date
from types import SimpleNamespace
import pytest
from bank_intel.collection import generic_source_engine as engine

@pytest.mark.parametrize(
    "production,expected",
    [(True, 121), (False, 100)]
)
def test_complete_production_discovery(
    monkeypatch, tmp_path, production, expected
):
    urls = [
        f"https://www.themorning.lk/articles/FakeArticle{i}"
        for i in range(121)
    ]

    monkeypatch.setattr(
        engine, "discover_article_links",
        lambda profile: (urls, {"listing_status": 200})
    )

    calls = []

    def extract(url, profile):
        calls.append(url)
        return SimpleNamespace(
            blocked=False,
            valid=True,
            errors=[],
            published_at="2026-10-21T10:00:00+05:30"
        )

    monkeypatch.setattr(engine, "safe_extract_article", extract)
    monkeypatch.setattr(
        engine, "stage_article",
        lambda **kwargs: {"status": "UNCHANGED"}
    )

    result = engine.collect_and_stage(
        project_root=tmp_path,
        source_key="the_morning",
        profile={"name": "The Morning"},
        from_date=date(2026, 10, 19),
        to_date=date(2026, 10, 21),
        limit=100,
        production=production,
    )

    assert result["candidate_urls"] == expected
    assert result["all_discovered_urls"] == 121
    assert result["truncated_discovery_urls"] == 121 - expected
    assert len(calls) == expected
