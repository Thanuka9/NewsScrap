from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path


def _write(
    path: Path,
    rows: list[dict],
    fieldnames: list[str],
) -> None:
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


def test_merge_deduplicates_and_preserves_labels(
    tmp_path,
):
    project = tmp_path / "project"
    scripts = project / "scripts"
    scripts.mkdir(parents=True)

    source_script = (
        Path(__file__)
        .resolve()
        .parents[2]
        / "scripts"
        / "merge_review_batches.py"
    )

    script_copy = (
        scripts
        / "merge_review_batches.py"
    )

    script_copy.write_text(
        source_script.read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )

    fields = [
        "source_url",
        "headline",
        "human_relevance_label",
        "label_source",
    ]

    old = project / "old.csv"
    new = project / "new.csv"
    out = project / "merged.csv"

    _write(
        old,
        [
            {
                "source_url": "https://a",
                "headline": "A",
                "human_relevance_label": "WATCH",
                "label_source": "OLD",
            },
            {
                "source_url": "https://b",
                "headline": "B",
                "human_relevance_label": "NOISE",
                "label_source": "OLD",
            },
        ],
        fields,
    )

    _write(
        new,
        [
            {
                "source_url": "https://a",
                "headline": "A updated",
                "human_relevance_label": "WATCH",
                "label_source": "NEW",
            },
            {
                "source_url": "https://c",
                "headline": "C",
                "human_relevance_label": "RELEVANT",
                "label_source": "NEW",
            },
        ],
        fields,
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script_copy),
            "--input",
            str(old),
            "--input",
            str(new),
            "--output",
            str(out),
        ],
        cwd=str(project),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0

    with out.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        rows = list(
            csv.DictReader(file)
        )

    assert len(rows) == 3

    by_url = {
        row["source_url"]: row
        for row in rows
    }

    assert (
        by_url[
            "https://a"
        ][
            "human_relevance_label"
        ]
        == "WATCH"
    )

    assert (
        by_url[
            "https://a"
        ][
            "label_source"
        ]
        == "NEW"
    )
