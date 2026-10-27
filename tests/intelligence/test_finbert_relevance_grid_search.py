from pathlib import Path
import importlib.util
import json


# This file lives at:
# tests/intelligence/test_finbert_relevance_grid_search.py
#
# parents[0] = tests/intelligence
# parents[1] = tests
# parents[2] = project root
PROJECT_ROOT = Path(__file__).resolve().parents[2]

SCRIPT = (
    PROJECT_ROOT
    / "scripts"
    / "run_finbert_relevance_grid_search.py"
)

if not SCRIPT.exists():
    raise FileNotFoundError(
        f"Grid-search script not found: {SCRIPT}"
    )

spec = importlib.util.spec_from_file_location(
    "gridmod",
    SCRIPT,
)

if spec is None or spec.loader is None:
    raise RuntimeError(
        f"Could not load grid-search module from {SCRIPT}"
    )

gridmod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gridmod)


def test_run_name_is_stable():
    assert (
        gridmod.run_name(
            2e-5,
            3,
            5,
        )
        == "lr_2em05__chunks_3__sched_5"
    )


def test_sort_key_prefers_macro_then_relevant():
    rows = [
        {
            "macro_f1": 0.65,
            "relevant_f1": 0.60,
            "relevant_recall": 0.50,
        },
        {
            "macro_f1": 0.67,
            "relevant_f1": 0.50,
            "relevant_recall": 0.50,
        },
        {
            "macro_f1": 0.67,
            "relevant_f1": 0.55,
            "relevant_recall": 0.40,
        },
    ]

    ranked = sorted(
        rows,
        key=gridmod.sort_key,
        reverse=True,
    )

    assert (
        ranked[0]["relevant_f1"]
        == 0.55
    )


def test_completed_run_requires_manifest_and_metrics(
    tmp_path,
):
    run = tmp_path / "run"
    run.mkdir()

    assert not gridmod.completed_run(
        run
    )

    (
        run
        / "run_manifest.json"
    ).write_text(
        json.dumps(
            {
                "status": "SUCCESS",
            }
        ),
        encoding="utf-8",
    )

    assert not gridmod.completed_run(
        run
    )

    (
        run
        / "best_validation_metrics.json"
    ).write_text(
        json.dumps(
            {
                "macro_f1": 0.70,
            }
        ),
        encoding="utf-8",
    )

    assert gridmod.completed_run(
        run
    )
