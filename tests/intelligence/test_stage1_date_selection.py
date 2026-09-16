from pathlib import Path

from scripts.run_intelligence_stage1 import (
    load_existing_review_annotations,
    matches_target_date,
)


def test_published_date_overrides_folder_date():
    path = Path("data/staged/daily_ft/2026-09-14/article/v001.json")
    payload = {
        "published_at": "2026-09-11T10:00:00+05:30",
        "collection_run_date": "2026-09-14",
    }

    matched, method = matches_target_date(
        path=path,
        payload=payload,
        target_date="2026-09-14",
    )

    assert matched is False
    assert method is None


def test_published_date_matches_actual_article_date():
    path = Path("data/staged/daily_ft/2026-09-14/article/v001.json")
    payload = {
        "published_at": "2026-09-11T10:00:00+05:30",
        "collection_run_date": "2026-09-14",
    }

    matched, method = matches_target_date(
        path=path,
        payload=payload,
        target_date="2026-09-11",
    )

    assert matched is True
    assert method == "published_at"


def test_collection_date_is_only_legacy_fallback():
    path = Path("data/staged/daily_ft/2026-09-14/article/v001.json")
    payload = {"collection_run_date": "2026-09-14"}

    matched, method = matches_target_date(
        path=path,
        payload=payload,
        target_date="2026-09-14",
    )

    assert matched is True
    assert method == "collection_run_date_fallback"


def test_path_date_is_last_resort_fallback():
    path = Path("data/staged/daily_ft/2026-09-14/article/v001.json")
    payload = {}

    matched, method = matches_target_date(
        path=path,
        payload=payload,
        target_date="2026-09-14",
    )

    assert matched is True
    assert method == "path_date_fallback"


def test_existing_review_annotations_are_preserved(tmp_path):
    review = tmp_path / "stage1_review_2026-09-14.csv"
    review.write_text(
        "source_url,human_label,review_notes\n"
        "https://example.com/a,RELEVANT,checked by analyst\n",
        encoding="utf-8",
    )

    annotations = load_existing_review_annotations(review)
    assert annotations["https://example.com/a"] == {
        "human_label": "RELEVANT",
        "review_notes": "checked by analyst",
    }
