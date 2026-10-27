from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path


def write_csv(
    path: Path,
    rows: list[dict],
    fieldnames: list[str],
):
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    with path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(rows)


def test_reviewed_v3_applies_label_date_group_and_temporal_split(
    tmp_path,
):
    project = (
        tmp_path
        / "project"
    )

    scripts = (
        project
        / "scripts"
    )

    scripts.mkdir(
        parents=True
    )

    source_script = (
        Path(__file__)
        .resolve()
        .parents[2]
        / "scripts"
        / "build_reviewed_v3_dataset.py"
    )

    script_copy = (
        scripts
        / "build_reviewed_v3_dataset.py"
    )

    script_copy.write_text(
        source_script.read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )

    base_fields = [
        "source_url",
        "story_group_id",
        "split",
        "source_name",
        "headline",
        "article_text",
        "published_at",
        "target_relevance_label",
        "target_topic_labels",
        "label_source",
        "label_confidence",
        "review_status",
        "review_notes",
    ]

    base_rows = [
        {
            "source_url": "https://a",
            "story_group_id": "g-a",
            "split": "validation",
            "source_name": "x",
            "headline": "A",
            "article_text": "text",
            "published_at": "2026-08-02T10:00:00+05:30",
            "target_relevance_label": "RELEVANT",
            "target_topic_labels": "MARKET_RATE_FX_RISK",
            "label_source": "OLD",
            "label_confidence": "MEDIUM",
            "review_status": "DONE",
            "review_notes": "",
        },
        {
            "source_url": "https://b",
            "story_group_id": "g-b",
            "split": "validation",
            "source_name": "x",
            "headline": "B",
            "article_text": "text",
            "published_at": "2026-07-03",
            "target_relevance_label": "RELEVANT",
            "target_topic_labels": "PROFITABILITY_FINANCIAL_CONDITION",
            "label_source": "OLD",
            "label_confidence": "MEDIUM",
            "review_status": "DONE",
            "review_notes": "",
        },
        {
            "source_url": "https://c",
            "story_group_id": "g-c",
            "split": "validation",
            "source_name": "x",
            "headline": "C",
            "article_text": "text",
            "published_at": "2026-08-04",
            "target_relevance_label": "WATCH",
            "target_topic_labels": "OTHER_SUPERVISORY",
            "label_source": "OLD",
            "label_confidence": "MEDIUM",
            "review_status": "DONE",
            "review_notes": "",
        },
        {
            "source_url": "https://d",
            "story_group_id": "g-d",
            "split": "test",
            "source_name": "x",
            "headline": "D",
            "article_text": "text",
            "published_at": "2026-09-05",
            "target_relevance_label": "NOISE",
            "target_topic_labels": "NOT_APPLICABLE",
            "label_source": "OLD",
            "label_confidence": "HIGH",
            "review_status": "DONE",
            "review_notes": "",
        },
        {
            "source_url": "https://e",
            "story_group_id": "g-e",
            "split": "train",
            "source_name": "x",
            "headline": "E",
            "article_text": "text",
            "published_at": "2026-03-05",
            "target_relevance_label": "WATCH",
            "target_topic_labels": "MACRO_FINANCIAL_SYSTEMIC",
            "label_source": "OLD",
            "label_confidence": "HIGH",
            "review_status": "DONE",
            "review_notes": "",
        },
        {
            "source_url": "https://f",
            "story_group_id": "g-f",
            "split": "train",
            "source_name": "x",
            "headline": "F",
            "article_text": "text",
            "published_at": "2026-04-05",
            "target_relevance_label": "NOISE",
            "target_topic_labels": "NOT_APPLICABLE",
            "label_source": "OLD",
            "label_confidence": "HIGH",
            "review_status": "DONE",
            "review_notes": "",
        },
        {
            "source_url": "https://g",
            "story_group_id": "g-g",
            "split": "test",
            "source_name": "x",
            "headline": "G",
            "article_text": "text",
            "published_at": "2026-09-06",
            "target_relevance_label": "RELEVANT",
            "target_topic_labels": "CREDIT_RISK",
            "label_source": "OLD",
            "label_confidence": "HIGH",
            "review_status": "DONE",
            "review_notes": "",
        },
        {
            "source_url": "https://h",
            "story_group_id": "g-h",
            "split": "test",
            "source_name": "x",
            "headline": "H",
            "article_text": "text",
            "published_at": "2026-09-07",
            "target_relevance_label": "WATCH",
            "target_topic_labels": "CREDIT_RISK",
            "label_source": "OLD",
            "label_confidence": "HIGH",
            "review_status": "DONE",
            "review_notes": "",
        },
        {
            "source_url": "https://i",
            "story_group_id": "g-i",
            "split": "validation",
            "source_name": "x",
            "headline": "I",
            "article_text": "text",
            "published_at": "2026-07-06",
            "target_relevance_label": "NOISE",
            "target_topic_labels": "NOT_APPLICABLE",
            "label_source": "OLD",
            "label_confidence": "HIGH",
            "review_status": "DONE",
            "review_notes": "",
        },
        {
            "source_url": "https://j",
            "story_group_id": "g-j",
            "split": "train",
            "source_name": "x",
            "headline": "J",
            "article_text": "text",
            "published_at": "2026-05-06",
            "target_relevance_label": "RELEVANT",
            "target_topic_labels": "CREDIT_RISK",
            "label_source": "OLD",
            "label_confidence": "HIGH",
            "review_status": "DONE",
            "review_notes": "",
        },
        {
            "source_url": "https://k",
            "story_group_id": "g-k",
            "split": "validation",
            "source_name": "x",
            "headline": "K",
            "article_text": "text",
            "published_at": "2026-07-20",
            "target_relevance_label": "RELEVANT",
            "target_topic_labels": "REGULATORY_PRUDENTIAL_ACTION",
            "label_source": "OLD",
            "label_confidence": "HIGH",
            "review_status": "DONE",
            "review_notes": "",
        },
    ]

    base_path = (
        project
        / "base.csv"
    )

    write_csv(
        base_path,
        base_rows,
        base_fields,
    )

    audit_fields = [
        "source_url",
        "reference_label_provisional",
        "reviewer_relevance_label",
        "reviewer_topic_labels",
        "reviewer_scope_rationale",
        "reviewer_name_or_id",
        "reviewer_reviewed_at",
        "reviewer_status",
        "review_scope",
        "review_evidence_status",
        "review_confidence",
        "review_date_flag",
        "suggested_publication_date",
        "suggested_story_group_id",
        "review_provenance",
    ]

    audit_rows = [
        {
            "source_url": "https://a",
            "reference_label_provisional": "RELEVANT",
            "reviewer_relevance_label": "RELEVANT",
            "reviewer_topic_labels": "MARKET_RATE_FX_RISK;REGULATORY_PRUDENTIAL_ACTION",
            "reviewer_scope_rationale": "Direct CBSL action.",
            "reviewer_name_or_id": "AI",
            "reviewer_reviewed_at": "2026-09-22",
            "reviewer_status": "AI_REVIEWED_NOT_HUMAN_GOLD",
            "review_scope": "SRI_LANKA_BANKING_SUPERVISION",
            "review_evidence_status": "FULL_PRIMARY_ARTICLE_READ",
            "review_confidence": "HIGH",
            "review_date_flag": "DATE_MISMATCH: corrected",
            "suggested_publication_date": "2026-01-09",
            "suggested_story_group_id": "",
            "review_provenance": "AI-assisted",
        },
        {
            "source_url": "https://b",
            "reference_label_provisional": "RELEVANT",
            "reviewer_relevance_label": "WATCH",
            "reviewer_topic_labels": "PROFITABILITY_FINANCIAL_CONDITION",
            "reviewer_scope_rationale": "Monitoring lead.",
            "reviewer_name_or_id": "AI",
            "reviewer_reviewed_at": "2026-09-22",
            "reviewer_status": "AI_REVIEWED_NOT_HUMAN_GOLD",
            "review_scope": "SRI_LANKA_BANKING_SUPERVISION",
            "review_evidence_status": "ARTICLE_PREVIEW_ONLY",
            "review_confidence": "MEDIUM",
            "review_date_flag": "",
            "suggested_publication_date": "",
            "suggested_story_group_id": "manual-group",
            "review_provenance": "AI-assisted",
        },
        {
            "source_url": "https://c",
            "reference_label_provisional": "WATCH",
            "reviewer_relevance_label": "WATCH",
            "reviewer_topic_labels": "OTHER_SUPERVISORY",
            "reviewer_scope_rationale": "Same continuing story.",
            "reviewer_name_or_id": "AI",
            "reviewer_reviewed_at": "2026-09-22",
            "reviewer_status": "AI_REVIEWED_NOT_HUMAN_GOLD",
            "review_scope": "SRI_LANKA_BANKING_SUPERVISION",
            "review_evidence_status": "FULL_PRIMARY_ARTICLE_READ",
            "review_confidence": "HIGH",
            "review_date_flag": "",
            "suggested_publication_date": "",
            "suggested_story_group_id": "manual-group",
            "review_provenance": "AI-assisted",
        },
    ]

    audit_path = (
        project
        / "audit.csv"
    )

    write_csv(
        audit_path,
        audit_rows,
        audit_fields,
    )

    output_root = (
        project
        / "out"
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script_copy),
            "--base-dataset",
            str(base_path),
            "--audit-file",
            str(audit_path),
            "--output-root",
            str(output_root),
        ],
        cwd=str(project),
        capture_output=True,
        text=True,
    )

    assert (
        result.returncode
        == 0
    ), result.stderr + result.stdout

    with (
        output_root
        / "dataset.csv"
    ).open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        rows = list(
            csv.DictReader(file)
        )

    by_url = {
        row["source_url"]: row
        for row in rows
    }

    # Date correction moves A from validation to train.
    assert (
        by_url[
            "https://a"
        ][
            "published_at"
        ]
        == "2026-01-09"
    )

    assert (
        by_url[
            "https://a"
        ][
            "split"
        ]
        == "train"
    )

    # Reviewed label change is applied.
    assert (
        by_url[
            "https://b"
        ][
            "target_relevance_label"
        ]
        == "WATCH"
    )

    # Manual group keeps both B and C together.
    assert (
        by_url[
            "https://b"
        ][
            "story_group_id"
        ]
        == "manual-group"
    )

    assert (
        by_url[
            "https://c"
        ][
            "story_group_id"
        ]
        == "manual-group"
    )

    assert (
        by_url[
            "https://b"
        ][
            "split"
        ]
        == by_url[
            "https://c"
        ][
            "split"
        ]
    )

    manifest = json.loads(
        (
            output_root
            / "manifest.json"
        ).read_text(
            encoding="utf-8"
        )
    )

    assert (
        manifest[
            "human_gold"
        ]
        is False
    )

    assert (
        manifest[
            "story_group_leakage_count"
        ]
        == 0
    )

    assert (
        manifest[
            "label_changes"
        ]
        == 1
    )

    assert (
        manifest[
            "publication_date_changes"
        ]
        == 1
    )

    assert (
        manifest[
            "story_group_changes"
        ]
        == 2
    )
