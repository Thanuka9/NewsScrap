
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from statistics import mean


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

REVIEW_ROOT = (
    PROJECT_ROOT
    / "data"
    / "review"
)

OUTPUT_ROOT = (
    PROJECT_ROOT
    / "data"
    / "review"
    / "historical_stage1_audit"
)


def parse_args():
    p = argparse.ArgumentParser(
        description=(
            "Audit historical Stage-1 decisions before Phase-2 ML."
        )
    )
    p.add_argument(
        "--start-date",
        required=True,
        help="YYYY-MM-DD",
    )
    p.add_argument(
        "--end-date",
        required=True,
        help="YYYY-MM-DD",
    )
    p.add_argument(
        "--sample-per-source-decision",
        type=int,
        default=15,
        help=(
            "Deterministic QA sample size for each "
            "source x gate-decision group. Default: 15."
        ),
    )
    p.add_argument(
        "--seed",
        type=int,
        default=20261008,
    )
    return p.parse_args()


def parse_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def date_from_review_filename(path: Path) -> str | None:
    stem = path.stem
    prefix = "stage1_review_"

    if not stem.startswith(prefix):
        return None

    value = stem[len(prefix):]

    try:
        date.fromisoformat(value)
        return value
    except ValueError:
        return None


def load_rows(
    *,
    start_date: str,
    end_date: str,
) -> tuple[list[dict], list[str]]:

    rows = []
    files = []

    for path in sorted(
        REVIEW_ROOT.glob(
            "stage1_review_*.csv"
        )
    ):
        run_date = (
            date_from_review_filename(
                path
            )
        )

        if run_date is None:
            continue

        if not (
            start_date
            <= run_date
            <= end_date
        ):
            continue

        files.append(
            str(path)
        )

        with path.open(
            "r",
            newline="",
            encoding="utf-8-sig",
        ) as f:
            reader = csv.DictReader(f)

            for row in reader:
                row = dict(row)
                row[
                    "publication_date"
                ] = run_date

                row[
                    "source_name"
                ] = str(
                    row.get(
                        "source_name",
                        "",
                    )
                    or "UNKNOWN"
                ).strip()

                row[
                    "gate_decision"
                ] = str(
                    row.get(
                        "gate_decision",
                        "",
                    )
                    or "UNKNOWN"
                ).strip()

                rows.append(
                    row
                )

    return (
        rows,
        files,
    )


def pct(
    numerator: int,
    denominator: int,
) -> float:

    if denominator == 0:
        return 0.0

    return round(
        100.0
        * numerator
        / denominator,
        2,
    )


def main():
    args = parse_args()

    start = (
        date.fromisoformat(
            args.start_date
        )
    )

    end = (
        date.fromisoformat(
            args.end_date
        )
    )

    if start > end:
        raise ValueError(
            "--start-date must be <= --end-date"
        )

    (
        rows,
        review_files,
    ) = load_rows(
        start_date=(
            start.isoformat()
        ),
        end_date=(
            end.isoformat()
        ),
    )

    if not rows:
        raise RuntimeError(
            "No Stage-1 review rows found "
            "inside the requested window."
        )

    total = len(
        rows
    )

    decision_counts = Counter(
        row[
            "gate_decision"
        ]
        for row
        in rows
    )

    source_counts = Counter(
        row[
            "source_name"
        ]
        for row
        in rows
    )

    by_source = defaultdict(
        Counter
    )

    by_month = defaultdict(
        Counter
    )

    source_scores = defaultdict(
        lambda: {
            "financial": [],
            "pr": [],
        }
    )

    for row in rows:
        source = (
            row[
                "source_name"
            ]
        )

        decision = (
            row[
                "gate_decision"
            ]
        )

        month = (
            row[
                "publication_date"
            ][:7]
        )

        by_source[
            source
        ][
            decision
        ] += 1

        by_month[
            month
        ][
            decision
        ] += 1

        financial = parse_float(
            row.get(
                "financial_score"
            )
        )

        pr_score = parse_float(
            row.get(
                "pr_score"
            )
        )

        if financial is not None:
            source_scores[
                source
            ][
                "financial"
            ].append(
                financial
            )

        if pr_score is not None:
            source_scores[
                source
            ][
                "pr"
            ].append(
                pr_score
            )

    # --------------------------------------------------------
    # QA flags: these are review priorities, NOT automatically
    # classification errors.
    # --------------------------------------------------------

    qa_flags = []

    for row in rows:
        decision = (
            row[
                "gate_decision"
            ]
        )

        financial = parse_float(
            row.get(
                "financial_score"
            )
        )

        pr_score = parse_float(
            row.get(
                "pr_score"
            )
        )

        reasons = []

        if (
            decision
            == "INCLUDE_CANDIDATE"
            and pr_score is not None
            and pr_score >= 50
        ):
            reasons.append(
                "INCLUDE_WITH_HIGH_PR_SCORE"
            )

        if (
            decision
            == "EXCLUDE_NOISE"
            and financial is not None
            and financial >= 70
        ):
            reasons.append(
                "NOISE_WITH_HIGH_FINANCIAL_SCORE"
            )

        if (
            decision
            == "WATCH"
            and financial is not None
            and financial < 25
        ):
            reasons.append(
                "WATCH_WITH_LOW_FINANCIAL_SCORE"
            )

        if reasons:
            flagged = dict(
                row
            )
            flagged[
                "qa_flag_reason"
            ] = ";".join(
                reasons
            )
            qa_flags.append(
                flagged
            )

    # --------------------------------------------------------
    # Deterministic stratified QA sample:
    # source x gate decision.
    # --------------------------------------------------------

    rng = random.Random(
        args.seed
    )

    grouped = defaultdict(
        list
    )

    for row in rows:
        grouped[
            (
                row[
                    "source_name"
                ],
                row[
                    "gate_decision"
                ],
            )
        ].append(
            row
        )

    sample_rows = []

    for key in sorted(
        grouped
    ):
        values = list(
            grouped[
                key
            ]
        )

        rng.shuffle(
            values
        )

        selected = values[
            :
            args.sample_per_source_decision
        ]

        for row in selected:
            item = dict(
                row
            )
            item[
                "qa_sample_group"
            ] = (
                f"{key[0]} | "
                f"{key[1]}"
            )
            sample_rows.append(
                item
            )

    OUTPUT_ROOT.mkdir(
        parents=True,
        exist_ok=True,
    )

    timestamp = (
        datetime.now()
        .strftime(
            "%Y%m%d_%H%M%S"
        )
    )

    summary_path = (
        OUTPUT_ROOT
        / (
            f"stage1_audit_"
            f"{args.start_date}_to_"
            f"{args.end_date}_"
            f"{timestamp}.json"
        )
    )

    source_path = (
        OUTPUT_ROOT
        / (
            f"stage1_by_source_"
            f"{timestamp}.csv"
        )
    )

    month_path = (
        OUTPUT_ROOT
        / (
            f"stage1_by_month_"
            f"{timestamp}.csv"
        )
    )

    sample_path = (
        OUTPUT_ROOT
        / (
            f"stage1_qa_sample_"
            f"{timestamp}.csv"
        )
    )

    flags_path = (
        OUTPUT_ROOT
        / (
            f"stage1_qa_flags_"
            f"{timestamp}.csv"
        )
    )

    all_decisions = sorted(
        set(
            decision_counts
        )
        | {
            "INCLUDE_CANDIDATE",
            "WATCH",
            "EXCLUDE_NOISE",
        }
    )

    with source_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as f:
        fields = [
            "source_name",
            "total",
            *all_decisions,
            "keep_total",
            "keep_pct",
            "mean_financial_score",
            "mean_pr_score",
        ]

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()

        for source in sorted(
            source_counts
        ):
            counts = (
                by_source[
                    source
                ]
            )

            source_total = (
                source_counts[
                    source
                ]
            )

            keep_total = (
                counts[
                    "INCLUDE_CANDIDATE"
                ]
                + counts[
                    "WATCH"
                ]
            )

            financial_values = (
                source_scores[
                    source
                ][
                    "financial"
                ]
            )

            pr_values = (
                source_scores[
                    source
                ][
                    "pr"
                ]
            )

            record = {
                "source_name": (
                    source
                ),
                "total": (
                    source_total
                ),
                "keep_total": (
                    keep_total
                ),
                "keep_pct": (
                    pct(
                        keep_total,
                        source_total,
                    )
                ),
                "mean_financial_score": (
                    round(
                        mean(
                            financial_values
                        ),
                        2,
                    )
                    if financial_values
                    else ""
                ),
                "mean_pr_score": (
                    round(
                        mean(
                            pr_values
                        ),
                        2,
                    )
                    if pr_values
                    else ""
                ),
            }

            for decision in (
                all_decisions
            ):
                record[
                    decision
                ] = counts[
                    decision
                ]

            writer.writerow(
                record
            )

    with month_path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as f:
        fields = [
            "month",
            "total",
            *all_decisions,
            "keep_total",
            "keep_pct",
        ]

        writer = csv.DictWriter(
            f,
            fieldnames=fields,
        )

        writer.writeheader()

        for month in sorted(
            by_month
        ):
            counts = (
                by_month[
                    month
                ]
            )

            month_total = sum(
                counts.values()
            )

            keep_total = (
                counts[
                    "INCLUDE_CANDIDATE"
                ]
                + counts[
                    "WATCH"
                ]
            )

            record = {
                "month": month,
                "total": (
                    month_total
                ),
                "keep_total": (
                    keep_total
                ),
                "keep_pct": (
                    pct(
                        keep_total,
                        month_total,
                    )
                ),
            }

            for decision in (
                all_decisions
            ):
                record[
                    decision
                ] = counts[
                    decision
                ]

            writer.writerow(
                record
            )

    def write_rows(
        path: Path,
        values: list[dict],
    ):
        if values:
            fields = []

            for row in values:
                for key in row:
                    if key not in fields:
                        fields.append(
                            key
                        )

        else:
            fields = [
                "source_name",
                "headline",
                "published_at",
                "source_url",
                "gate_decision",
            ]

        with path.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as f:
            writer = csv.DictWriter(
                f,
                fieldnames=fields,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(
                values
            )

    write_rows(
        sample_path,
        sample_rows,
    )

    write_rows(
        flags_path,
        qa_flags,
    )

    keep_total = (
        decision_counts[
            "INCLUDE_CANDIDATE"
        ]
        + decision_counts[
            "WATCH"
        ]
    )

    summary = {
        "generated_at": (
            datetime.now()
            .isoformat()
        ),
        "window": {
            "start_date": (
                start.isoformat()
            ),
            "end_date": (
                end.isoformat()
            ),
        },
        "review_files": (
            len(
                review_files
            )
        ),
        "total_articles": (
            total
        ),
        "gate_decisions": (
            dict(
                decision_counts
            )
        ),
        "keep_total": (
            keep_total
        ),
        "keep_pct": (
            pct(
                keep_total,
                total,
            )
        ),
        "source_totals": (
            dict(
                source_counts
            )
        ),
        "qa_flag_count": (
            len(
                qa_flags
            )
        ),
        "qa_sample_count": (
            len(
                sample_rows
            )
        ),
        "outputs": {
            "by_source": (
                str(
                    source_path
                )
            ),
            "by_month": (
                str(
                    month_path
                )
            ),
            "qa_sample": (
                str(
                    sample_path
                )
            ),
            "qa_flags": (
                str(
                    flags_path
                )
            ),
        },
    }

    summary_path.write_text(
        json.dumps(
            summary,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        "=" * 88
    )

    print(
        "HISTORICAL STAGE-1 QA"
    )

    print(
        "=" * 88
    )

    print(
        f"Window             : "
        f"{start} -> {end}"
    )

    print(
        f"Review files       : "
        f"{len(review_files)}"
    )

    print(
        f"Articles           : "
        f"{total}"
    )

    print()

    print(
        "GATE DECISIONS"
    )

    for decision in (
        "INCLUDE_CANDIDATE",
        "WATCH",
        "EXCLUDE_NOISE",
    ):
        count = (
            decision_counts[
                decision
            ]
        )

        print(
            f"  {decision:20} "
            f"{count:5d} "
            f"({pct(count, total):6.2f}%)"
        )

    print()

    print(
        f"KEEP (Include+Watch): "
        f"{keep_total} "
        f"({pct(keep_total, total):.2f}%)"
    )

    print()

    print(
        "BY SOURCE"
    )

    for source in sorted(
        source_counts
    ):
        counts = (
            by_source[
                source
            ]
        )

        source_total = (
            source_counts[
                source
            ]
        )

        keep = (
            counts[
                "INCLUDE_CANDIDATE"
            ]
            + counts[
                "WATCH"
            ]
        )

        print(
            f"  {source:24} "
            f"total={source_total:5d} "
            f"include={counts['INCLUDE_CANDIDATE']:4d} "
            f"watch={counts['WATCH']:4d} "
            f"noise={counts['EXCLUDE_NOISE']:4d} "
            f"keep={pct(keep, source_total):6.2f}%"
        )

    print()

    print(
        f"QA flags           : "
        f"{len(qa_flags)} "
        "(review priorities, not automatic errors)"
    )

    print(
        f"QA sample          : "
        f"{len(sample_rows)}"
    )

    print()

    print(
        f"Summary JSON       : "
        f"{summary_path}"
    )

    print(
        f"By source CSV      : "
        f"{source_path}"
    )

    print(
        f"By month CSV       : "
        f"{month_path}"
    )

    print(
        f"QA sample CSV      : "
        f"{sample_path}"
    )

    print(
        f"QA flags CSV       : "
        f"{flags_path}"
    )


if __name__ == "__main__":
    main()
