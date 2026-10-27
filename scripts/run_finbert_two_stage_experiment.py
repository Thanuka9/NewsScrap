from __future__ import annotations

"""
Run the fixed first two-stage FinBERT experiment.

This wrapper:
* validates Stage A and Stage B,
* trains Stage A and Stage B sequentially,
* uses one fixed configuration,
* never evaluates test.csv,
* reuses successful runs when --resume is enabled.
"""

import argparse
import json
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]

TRAINER = (
    PROJECT_ROOT
    / "scripts"
    / "run_finbert_two_stage_finetune.py"
)

OUTPUT_BASE = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_two_stage"
    / "supervisory_relevance_v4_two_stage"
)

TASK_OUTPUTS = {
    "stage-a": (
        OUTPUT_BASE
        / "stage_a_noise_vs_keep"
        / "finbert_lr1e-05_chunks5_sched5_seed42"
    ),
    "stage-b": (
        OUTPUT_BASE
        / "stage_b_watch_vs_relevant"
        / "finbert_lr1e-05_chunks5_sched5_seed42"
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run fixed Stage A and Stage B "
            "FinBERT experiment without touching test.csv."
        )
    )

    parser.add_argument(
        "--device",
        choices=(
            "auto",
            "cpu",
            "cuda",
            "mps",
        ),
        default="auto",
    )

    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
    )

    parser.add_argument(
        "--validate-only",
        action="store_true",
        help=(
            "Validate both datasets and stop "
            "before model loading/training."
        ),
    )

    return parser.parse_args()


def completed(output: Path) -> bool:
    manifest = (
        output
        / "run_manifest.json"
    )

    metrics = (
        output
        / "best_validation_metrics.json"
    )

    if not (
        manifest.is_file()
        and metrics.is_file()
    ):
        return False

    try:
        payload = json.loads(
            manifest.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return False

    return (
        payload.get("status")
        == "SUCCESS"
    )


def run_command(
    cmd: list[str],
) -> None:
    print()
    print(
        "COMMAND:",
        " ".join(cmd),
    )
    subprocess.run(
        cmd,
        check=True,
    )


def main() -> None:
    args = parse_args()

    if not TRAINER.is_file():
        raise FileNotFoundError(
            TRAINER
        )

    print("=" * 96)
    print(
        "FIXED TWO-STAGE FINBERT EXPERIMENT"
    )
    print("=" * 96)
    print(
        "Configuration:"
    )
    print(
        "  learning rate       : 1e-5"
    )
    print(
        "  max body chunks     : 5"
    )
    print(
        "  epochs              : 5"
    )
    print(
        "  scheduler horizon   : 5"
    )
    print(
        "  weight decay        : 0.01"
    )
    print(
        "  warmup ratio        : 0.10"
    )
    print(
        "  gradient accumulation: 4"
    )
    print(
        "  seed                : 42"
    )
    print(
        "  test access         : DISABLED"
    )

    # Validate both task datasets first.
    for task in (
        "stage-a",
        "stage-b",
    ):
        run_command(
            [
                sys.executable,
                str(TRAINER),
                "--task",
                task,
                "--validate-only",
            ]
        )

    if args.validate_only:
        print()
        print(
            "VALIDATION-ONLY COMPLETE; "
            "no model training and no test access."
        )
        return

    for task in (
        "stage-a",
        "stage-b",
    ):
        output = TASK_OUTPUTS[
            task
        ]

        print()
        print("-" * 96)
        print(
            f"TRAIN {task.upper()}"
        )
        print("-" * 96)

        if (
            args.resume
            and completed(output)
        ):
            print(
                "Completed run found; reusing:",
                output,
            )
            continue

        if output.exists():
            if args.resume:
                raise RuntimeError(
                    "Incomplete output directory exists. "
                    "For audit safety it was NOT deleted automatically. "
                    "Inspect/remove it explicitly before rerunning: "
                    f"{output}"
                )
            raise RuntimeError(
                f"Output already exists: {output}"
            )

        run_command(
            [
                sys.executable,
                str(TRAINER),
                "--task",
                task,
                "--output-root",
                str(output),
                "--epochs",
                "5",
                "--scheduler-epochs",
                "5",
                "--learning-rate",
                "1e-5",
                "--weight-decay",
                "0.01",
                "--warmup-ratio",
                "0.10",
                "--max-body-chunks",
                "5",
                "--gradient-accumulation",
                "4",
                "--max-grad-norm",
                "1.0",
                "--patience",
                "5",
                "--seed",
                "42",
                "--device",
                args.device,
            ]
        )

    print()
    print("=" * 96)
    print(
        "TWO-STAGE TRAINING COMPLETE"
    )
    print("=" * 96)

    for task in (
        "stage-a",
        "stage-b",
    ):
        output = TASK_OUTPUTS[
            task
        ]

        manifest = json.loads(
            (
                output
                / "run_manifest.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        metrics = json.loads(
            (
                output
                / "best_validation_metrics.json"
            ).read_text(
                encoding="utf-8"
            )
        )

        print(
            f"{task.upper():<8} "
            f"best_epoch={manifest['best_epoch']} "
            f"macro_F1={metrics['macro_f1']:.4f} "
            f"{metrics['focus_label']}_F1="
            f"{metrics['focus_f1']:.4f} "
            f"{metrics['focus_label']}_recall="
            f"{metrics['focus_recall']:.4f}"
        )

    print()
    print(
        "IMPORTANT: test.csv was NOT evaluated."
    )


if __name__ == "__main__":
    main()
