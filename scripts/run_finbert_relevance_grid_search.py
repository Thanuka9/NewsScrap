from __future__ import annotations

import argparse
import csv
import itertools
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DATASET = (
    PROJECT_ROOT
    / "data"
    / "training"
    / "datasets"
    / "supervisory_relevance_v3_reviewed"
)

DEFAULT_TRAINER = (
    PROJECT_ROOT
    / "scripts"
    / "run_finbert_relevance_finetune.py"
)

DEFAULT_OUTPUT_ROOT = (
    PROJECT_ROOT
    / "data"
    / "processed"
    / "relevance_grid"
    / "supervisory_relevance_v3_grid1"
)


def parse_float_list(value: str) -> list[float]:
    items = [x.strip() for x in value.split(",") if x.strip()]
    if not items:
        raise argparse.ArgumentTypeError("Expected at least one float value")
    try:
        values = [float(x) for x in items]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if any(x <= 0 for x in values):
        raise argparse.ArgumentTypeError("All float values must be > 0")
    return values


def parse_int_list(value: str) -> list[int]:
    items = [x.strip() for x in value.split(",") if x.strip()]
    if not items:
        raise argparse.ArgumentTypeError("Expected at least one integer value")
    try:
        values = [int(x) for x in items]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc
    if any(x < 1 for x in values):
        raise argparse.ArgumentTypeError("All integer values must be >= 1")
    return values


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Controlled grid search wrapper for the FinBERT supervisory-relevance "
            "fine-tuner. The test split is never evaluated by this script."
        )
    )
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--trainer", type=Path, default=DEFAULT_TRAINER)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)

    parser.add_argument(
        "--learning-rates",
        type=parse_float_list,
        default=[1e-5, 2e-5, 3e-5],
        help="Comma-separated learning rates. Default: 1e-5,2e-5,3e-5",
    )
    parser.add_argument(
        "--body-chunks",
        type=parse_int_list,
        default=[3, 5],
        help="Comma-separated max body chunks. Default: 3,5",
    )
    parser.add_argument(
        "--schedule-epochs",
        type=parse_int_list,
        default=[3, 5],
        help=(
            "Comma-separated scheduler/training horizons. Each run uses "
            "--epochs N and --scheduler-epochs N. Default: 3,5"
        ),
    )

    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-ratio", type=float, default=0.10)
    parser.add_argument("--gradient-accumulation", type=int, default=4)
    parser.add_argument("--max-grad-norm", type=float, default=1.0)
    parser.add_argument(
        "--patience",
        type=int,
        default=5,
        help=(
            "Default 5 so the 3- and 5-epoch schedules complete instead of "
            "being truncated by early stopping."
        ),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--device",
        choices=("auto", "cpu", "cuda", "mps"),
        default="auto",
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip runs with a completed run_manifest.json. Default: true",
    )
    parser.add_argument(
        "--validate-first",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run trainer --validate-only once before the grid. Default: true",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the grid without training.",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=None,
        help="Optional cap for testing the wrapper.",
    )
    return parser.parse_args()


def resolve_path(path: Path) -> Path:
    return path if path.is_absolute() else PROJECT_ROOT / path


def lr_slug(value: float) -> str:
    text = f"{value:.0e}".replace("+", "")
    return text.replace("-", "m")


def run_name(lr: float, chunks: int, horizon: int) -> str:
    return f"lr_{lr_slug(lr)}__chunks_{chunks}__sched_{horizon}"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def completed_run(run_dir: Path) -> bool:
    manifest = run_dir / "run_manifest.json"
    metrics = run_dir / "best_validation_metrics.json"
    if not manifest.is_file() or not metrics.is_file():
        return False
    try:
        data = load_json(manifest)
    except Exception:
        return False
    return data.get("status") == "SUCCESS"


def result_from_run(
    run_dir: Path,
    lr: float,
    chunks: int,
    horizon: int,
) -> dict[str, Any]:
    metrics = load_json(run_dir / "best_validation_metrics.json")
    manifest = load_json(run_dir / "run_manifest.json")

    return {
        "run_name": run_dir.name,
        "status": "SUCCESS",
        "learning_rate": lr,
        "max_body_chunks": chunks,
        "schedule_epochs": horizon,
        "epochs_requested": manifest.get("epochs_requested"),
        "epochs_completed": manifest.get("epochs_completed"),
        "best_epoch": manifest.get("best_epoch"),
        "macro_f1": metrics.get("macro_f1"),
        "balanced_accuracy": metrics.get("balanced_accuracy"),
        "accuracy": metrics.get("accuracy"),
        "relevant_f1": metrics.get("relevant_f1"),
        "relevant_precision": metrics.get("relevant_precision"),
        "relevant_recall": metrics.get("relevant_recall"),
        "watch_f1": metrics.get("watch_f1"),
        "noise_f1": metrics.get("noise_f1"),
        "output_dir": str(run_dir),
    }


def sort_key(row: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(row.get("macro_f1") or -1),
        float(row.get("relevant_f1") or -1),
        float(row.get("relevant_recall") or -1),
    )


def write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "rank",
        "run_name",
        "status",
        "learning_rate",
        "max_body_chunks",
        "schedule_epochs",
        "epochs_requested",
        "epochs_completed",
        "best_epoch",
        "macro_f1",
        "balanced_accuracy",
        "accuracy",
        "relevant_f1",
        "relevant_precision",
        "relevant_recall",
        "watch_f1",
        "noise_f1",
        "output_dir",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for rank, row in enumerate(rows, start=1):
            out = dict(row)
            out["rank"] = rank
            writer.writerow({key: out.get(key, "") for key in fields})


def main() -> None:
    args = parse_args()

    dataset_root = resolve_path(args.dataset_root)
    trainer = resolve_path(args.trainer)
    output_root = resolve_path(args.output_root)

    if not dataset_root.is_dir():
        raise FileNotFoundError(f"Dataset root not found: {dataset_root}")
    if not trainer.is_file():
        raise FileNotFoundError(f"Trainer not found: {trainer}")
    if args.weight_decay < 0:
        raise ValueError("--weight-decay must be >= 0")
    if not 0 <= args.warmup_ratio < 1:
        raise ValueError("--warmup-ratio must be in [0, 1)")
    if args.gradient_accumulation < 1:
        raise ValueError("--gradient-accumulation must be >= 1")
    if args.patience < 1:
        raise ValueError("--patience must be >= 1")
    if args.max_runs is not None and args.max_runs < 1:
        raise ValueError("--max-runs must be >= 1")

    configs = list(
        itertools.product(
            args.learning_rates,
            args.body_chunks,
            args.schedule_epochs,
        )
    )
    if args.max_runs is not None:
        configs = configs[: args.max_runs]

    print("=" * 88)
    print("FINBERT SUPERVISORY RELEVANCE GRID SEARCH")
    print("=" * 88)
    print("Dataset:", dataset_root)
    print("Trainer:", trainer)
    print("Output root:", output_root)
    print("Grid size:", len(configs))
    print("Learning rates:", args.learning_rates)
    print("Body chunks:", args.body_chunks)
    print("Schedule/training epochs:", args.schedule_epochs)
    print("Fixed weight decay:", args.weight_decay)
    print("Fixed warmup ratio:", args.warmup_ratio)
    print("Fixed gradient accumulation:", args.gradient_accumulation)
    print("Fixed max grad norm:", args.max_grad_norm)
    print("Fixed seed:", args.seed)
    print("IMPORTANT: test.csv will NOT be evaluated by this wrapper.")
    print()

    for i, (lr, chunks, horizon) in enumerate(configs, start=1):
        print(
            f"{i:02d}. {run_name(lr, chunks, horizon)} "
            f"(lr={lr:g}, chunks={chunks}, epochs/scheduler={horizon})"
        )

    if args.dry_run:
        print("\nDRY RUN complete; no training started.")
        return

    output_root.mkdir(parents=True, exist_ok=True)

    if args.validate_first:
        print("\nRunning one dataset validation audit before grid...")
        cmd = [
            sys.executable,
            str(trainer),
            "--dataset-root",
            str(dataset_root),
            "--validate-only",
        ]
        subprocess.run(cmd, check=True)

    results: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for index, (lr, chunks, horizon) in enumerate(configs, start=1):
        name = run_name(lr, chunks, horizon)
        run_dir = output_root / name

        print("\n" + "-" * 88)
        print(f"GRID RUN {index}/{len(configs)}: {name}")
        print("-" * 88)

        if args.resume and completed_run(run_dir):
            print("Completed run found; reusing existing result.")
            results.append(result_from_run(run_dir, lr, chunks, horizon))
            continue

        if run_dir.exists():
            # The trainer intentionally refuses to overwrite an experiment directory.
            # Remove only incomplete run directories when --resume is enabled.
            if args.resume:
                print("Removing incomplete run directory:", run_dir)
                import shutil
                shutil.rmtree(run_dir)
            else:
                raise RuntimeError(
                    f"Run directory already exists: {run_dir}. "
                    "Use --resume or remove it explicitly."
                )

        cmd = [
            sys.executable,
            str(trainer),
            "--dataset-root",
            str(dataset_root),
            "--output-root",
            str(run_dir),
            "--epochs",
            str(horizon),
            "--scheduler-epochs",
            str(horizon),
            "--learning-rate",
            str(lr),
            "--weight-decay",
            str(args.weight_decay),
            "--warmup-ratio",
            str(args.warmup_ratio),
            "--max-body-chunks",
            str(chunks),
            "--gradient-accumulation",
            str(args.gradient_accumulation),
            "--max-grad-norm",
            str(args.max_grad_norm),
            "--patience",
            str(args.patience),
            "--seed",
            str(args.seed),
            "--device",
            args.device,
        ]

        print("Command:")
        print(" ".join(cmd))

        try:
            subprocess.run(cmd, check=True)
            results.append(result_from_run(run_dir, lr, chunks, horizon))
        except subprocess.CalledProcessError as exc:
            failures.append(
                {
                    "run_name": name,
                    "learning_rate": lr,
                    "max_body_chunks": chunks,
                    "schedule_epochs": horizon,
                    "returncode": exc.returncode,
                    "output_dir": str(run_dir),
                }
            )
            print(f"RUN FAILED: {name} (return code {exc.returncode})")
        except Exception as exc:
            failures.append(
                {
                    "run_name": name,
                    "learning_rate": lr,
                    "max_body_chunks": chunks,
                    "schedule_epochs": horizon,
                    "returncode": "",
                    "error": repr(exc),
                    "output_dir": str(run_dir),
                }
            )
            print(f"RUN FAILED: {name}: {exc!r}")

        ranked = sorted(results, key=sort_key, reverse=True)
        write_summary_csv(output_root / "grid_results.csv", ranked)
        (output_root / "grid_results.json").write_text(
            json.dumps(ranked, indent=2),
            encoding="utf-8",
        )
        (output_root / "grid_failures.json").write_text(
            json.dumps(failures, indent=2),
            encoding="utf-8",
        )

        if ranked:
            best = ranked[0]
            print(
                "CURRENT BEST:",
                best["run_name"],
                f"macro-F1={best['macro_f1']:.4f}",
                f"RELEVANT-F1={best['relevant_f1']:.4f}",
                f"RELEVANT-recall={best['relevant_recall']:.4f}",
            )

    ranked = sorted(results, key=sort_key, reverse=True)
    write_summary_csv(output_root / "grid_results.csv", ranked)
    (output_root / "grid_results.json").write_text(
        json.dumps(ranked, indent=2),
        encoding="utf-8",
    )
    (output_root / "grid_failures.json").write_text(
        json.dumps(failures, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 88)
    print("GRID SEARCH COMPLETE")
    print("=" * 88)
    print("Successful runs:", len(ranked))
    print("Failed runs:", len(failures))
    print("Results CSV:", output_root / "grid_results.csv")
    print("Results JSON:", output_root / "grid_results.json")

    if ranked:
        best = ranked[0]
        print("\nBEST VALIDATION CONFIGURATION")
        print("Run:", best["run_name"])
        print("Learning rate:", best["learning_rate"])
        print("Body chunks:", best["max_body_chunks"])
        print("Schedule/training epochs:", best["schedule_epochs"])
        print("Best epoch:", best["best_epoch"])
        print("Macro-F1:", round(float(best["macro_f1"]), 4))
        print("RELEVANT F1:", round(float(best["relevant_f1"]), 4))
        print("RELEVANT recall:", round(float(best["relevant_recall"]), 4))
        print("\nTest split was NOT evaluated.")


if __name__ == "__main__":
    main()
