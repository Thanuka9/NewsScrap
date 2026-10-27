

import csv
import subprocess
import sys
from pathlib import Path


def _write_csv(
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


def test_carries_labels_and_provenance(
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
        / "carry_forward_review_labels.py"
    )

    script_copy = (
        scripts
        / "carry_forward_review_labels.py"
    )
    script_copy.write_text(
        source_script.read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )

    source = project / "old.csv"
    target = project / "new.csv"

    _write_csv(
        source,
        [
            {
                "source_url": "https://a",
                "human_relevance_label": "RELEVANT",
                "human_topic_labels": "CAPITAL_SOLVENCY",
                "review_status": "DONE",
                "human_notes": "note",
                "label_source": "AI_PROVISIONAL",
                "label_confidence": "HIGH",
            }
        ],
        [
            "source_url",
            "human_relevance_label",
            "human_topic_labels",
            "review_status",
            "human_notes",
            "label_source",
            "label_confidence",
        ],
    )

    _write_csv(
        target,
        [
            {
                "source_url": "https://a",
                "headline": "A",
                "human_relevance_label": "",
                "human_topic_labels": "",
                "review_status": "",
                "human_notes": "",
            },
            {
                "source_url": "https://b",
                "headline": "B",
                "human_relevance_label": "",
                "human_topic_labels": "",
                "review_status": "",
                "human_notes": "",
            },
        ],
        [
            "source_url",
            "headline",
            "human_relevance_label",
            "human_topic_labels",
            "review_status",
            "human_notes",
        ],
    )

    result = subprocess.run(
        [
            sys.executable,
            str(script_copy),
            "--source",
            str(source),
            "--target",
            str(target),
        ],
        cwd=str(project),
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0

    with target.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as file:
        rows = list(
            csv.DictReader(file)
        )

    assert (
        rows[0][
            "human_relevance_label"
        ]
        == "RELEVANT"
    )
    assert (
        rows[0][
            "label_source"
        ]
        == "AI_PROVISIONAL"
    )
    assert (
        rows[1][
            "human_relevance_label"
        ]
        == ""
    )
