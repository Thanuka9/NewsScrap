from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from run_finbert_relevance_finetune import (  # noqa: E402
    LABEL2ID,
    digest_rows,
    output_has_training_artifacts,
    parse_args,
    spaced_indices,
    validate_splits,
)


def row(label: str, group: str, url: str) -> dict[str, str]:
    return {
        "source_url": url,
        "story_group_id": group,
        "target_relevance_label": label,
        "headline": "Test",
        "article_text": "Body",
        "published_at": "2026-05-01",
        "label_source": "AI_PROVISIONAL",
    }


def make_valid_splits():
    train = [
        row(label, f"g{i}", f"https://train/{i}")
        for i, label in enumerate(LABEL2ID)
    ]
    validation = [
        row(label, f"v{i}", f"https://val/{i}")
        for i, label in enumerate(LABEL2ID)
    ]
    return train, validation


def test_validation_and_provenance():
    train, validation = make_valid_splits()
    audit = validate_splits(train, validation)
    assert audit["train_val_group_overlap"] == 0
    assert audit["provenance"] == {"AI_PROVISIONAL": 6}


def test_leakage_rejected():
    train, validation = make_valid_splits()
    validation[0]["story_group_id"] = train[0]["story_group_id"]
    with pytest.raises(ValueError, match="leakage"):
        validate_splits(train, validation)


def test_duplicate_url_rejected():
    train, validation = make_valid_splits()
    train[1]["source_url"] = train[0]["source_url"]
    with pytest.raises(ValueError, match="Duplicate source_url"):
        validate_splits(train, validation)


def test_body_chunks_cover_beginning_middle_end():
    assert spaced_indices(10, 3) == [0, 4, 9]
    assert spaced_indices(2, 3) == [0, 1]
    assert spaced_indices(10, 1) == [0]


def test_signature_changes_on_label_change():
    original = [row("NOISE", "g", "https://a")]
    changed = [dict(original[0], target_relevance_label="WATCH")]
    assert digest_rows(original) != digest_rows(changed)


def test_scheduler_epochs_defaults_to_epochs(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_finbert_relevance_finetune.py", "--epochs", "4"],
    )
    args = parse_args()
    assert args.epochs == 4
    assert args.scheduler_epochs == 4


def test_scheduler_epochs_can_exceed_training_epochs(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_finbert_relevance_finetune.py",
            "--epochs",
            "3",
            "--scheduler-epochs",
            "5",
            "--patience",
            "5",
        ],
    )
    args = parse_args()
    assert args.epochs == 3
    assert args.scheduler_epochs == 5
    assert args.patience == 5


def test_scheduler_epochs_cannot_be_shorter_than_training(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_finbert_relevance_finetune.py",
            "--epochs",
            "5",
            "--scheduler-epochs",
            "3",
        ],
    )
    with pytest.raises(SystemExit):
        parse_args()


def test_warmup_ratio_validation(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_finbert_relevance_finetune.py", "--warmup-ratio", "1.0"],
    )
    with pytest.raises(SystemExit):
        parse_args()


def test_nonempty_output_is_detected(tmp_path):
    assert output_has_training_artifacts(tmp_path) is False
    (tmp_path / "run_manifest.json").write_text("{}", encoding="utf-8")
    assert output_has_training_artifacts(tmp_path) is True
