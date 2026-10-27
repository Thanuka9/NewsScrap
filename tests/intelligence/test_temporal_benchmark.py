from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


def _write_csv(
    path: Path,
    rows: list[dict],
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = [
        "story_group_id",
        "split",
        "published_at",
        "target_relevance_label",
    ]

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


def test_temporal_group_assignment_uses_latest_date(
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
        / "build_temporal_benchmark.py"
    )

    script_copy = (
        scripts
        / "build_temporal_benchmark.py"
    )

    script_copy.write_text(
        source_script.read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )

    dataset = (
        project
        / "dataset.csv"
    )

    rows = [
        {
            "story_group_id": "g_train_noise",
            "split": "old",
            "published_at": "2026-02-01T10:00:00+05:30",
            "target_relevance_label": "NOISE",
        },
        {
            "story_group_id": "g_train_watch",
            "split": "old",
            "published_at": "2026-03-01",
            "target_relevance_label": "WATCH",
        },
        {
            "story_group_id": "g_train_rel",
            "split": "old",
            "published_at": "2026-04-01",
            "target_relevance_label": "RELEVANT",
        },
        {
            "story_group_id": "g_val_noise",
            "split": "old",
            "published_at": "2026-07-03",
            "target_relevance_label": "NOISE",
        },
        {
            "story_group_id": "g_val_watch",
            "split": "old",
            "published_at": "2026-07-04",
            "target_relevance_label": "WATCH",
        },
        {
            "story_group_id": "g_cross_rel",
            "split": "old",
            "published_at": "2026-06-30",
            "target_relevance_label": "RELEVANT",
        },
        {
            "story_group_id": "g_cross_rel",
            "split": "old",
            "published_at": "2026-07-02",
            "target_relevance_label": "RELEVANT",
        },
        {
            "story_group_id": "g_test_noise",
            "split": "old",
            "published_at": "2026-09-01",
            "target_relevance_label": "NOISE",
        },
        {
            "story_group_id": "g_test_watch",
            "split": "old",
            "published_at": "2026-09-02",
            "target_relevance_label": "WATCH",
        },
        {
            "story_group_id": "g_test_rel",
            "split": "old",
            "published_at": "2026-09-03",
            "target_relevance_label": "RELEVANT",
        },
    ]

    _write_csv(
        dataset,
        rows,
    )

    result = subprocess.run(
        [
            sys.executable,
            str(
                script_copy
            ),
            "--dataset-file",
            str(
                dataset
            ),
            "--validation-start",
            "2026-07-01",
            "--test-start",
            "2026-09-01",
            "--output-name",
            "temporal_test",
        ],
        cwd=str(
            project
        ),
        capture_output=True,
        text=True,
    )

    assert (
        result.returncode
        == 0
    )

    validation_file = (
        project
        / "data"
        / "training"
        / "datasets"
        / "temporal_test"
        / "validation.csv"
    )

    with validation_file.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        validation_rows = list(
            csv.DictReader(
                file
            )
        )

    cross_rows = [
        row
        for row in validation_rows
        if row[
            "story_group_id"
        ]
        == "g_cross_rel"
    ]

    assert len(
        cross_rows
    ) == 2
