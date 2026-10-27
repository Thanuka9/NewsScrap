from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STAGED_ROOT = PROJECT_ROOT / "data" / "staged"
DEFAULT_AUDIT_ROOT = PROJECT_ROOT / "data" / "review" / "historical_ml_audit"
TZ = ZoneInfo("Asia/Colombo")

PAIR_ORDER = [
    ("NOISE", "WATCH"),
    ("NOISE", "RELEVANT"),
    ("WATCH", "NOISE"),
    ("WATCH", "RELEVANT"),
    ("RELEVANT", "NOISE"),
    ("RELEVANT", "WATCH"),
]


def text(value) -> str:
    return str(value or "").strip()


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict], fields: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def latest_audit_dir() -> Path:
    candidates = sorted(
        p for p in DEFAULT_AUDIT_ROOT.iterdir()
        if p.is_dir() and (p / "disagreements_all.csv").exists()
    )
    if not candidates:
        raise FileNotFoundError(
            "No audit directory containing disagreements_all.csv was found. "
            "Pass --audit-dir explicitly."
        )
    return candidates[-1]


def build_staged_index() -> dict[str, dict]:
    """
    Index the newest staged article by source_url and canonical_url.
    This lets the evaluation sample contain full article text so
    adjudication is not based on headlines alone.
    """
    index: dict[str, dict] = {}

    if not STAGED_ROOT.exists():
        return index

    for path in STAGED_ROOT.rglob("*.json"):
        try:
            payload = load_json(path)
        except Exception:
            continue

        source_url = text(payload.get("source_url"))
        canonical_url = text(payload.get("canonical_url"))

        try:
            version = int(payload.get("article_version") or 0)
        except (TypeError, ValueError):
            version = 0

        record = {
            "payload": payload,
            "path": path,
            "version": version,
        }

        for key in {source_url, canonical_url}:
            if not key:
                continue
            previous = index.get(key)
            if previous is None or version >= previous["version"]:
                index[key] = record

    return index


def largest_remainder_allocation(
    counts: dict[str, int],
    target: int,
) -> dict[str, int]:
    """
    Allocate target proportionally across source counts, while
    preserving at least one item for every non-empty source when possible.
    """
    positive = {k: v for k, v in counts.items() if v > 0}
    if not positive or target <= 0:
        return {k: 0 for k in counts}

    target = min(target, sum(positive.values()))
    keys = sorted(positive)

    allocation = {k: 0 for k in counts}

    # Give one item to each represented source when possible.
    if target >= len(keys):
        for k in keys:
            allocation[k] = 1
        remaining = target - len(keys)
        residual_counts = {k: max(0, positive[k] - 1) for k in keys}
    else:
        # If target is smaller than source count, give slots to the largest sources.
        for k in sorted(keys, key=lambda x: (-positive[x], x))[:target]:
            allocation[k] = 1
        return allocation

    residual_total = sum(residual_counts.values())
    if remaining <= 0 or residual_total <= 0:
        return allocation

    raw = {
        k: remaining * residual_counts[k] / residual_total
        for k in keys
    }
    floors = {k: int(raw[k]) for k in keys}

    for k in keys:
        capacity = positive[k] - allocation[k]
        add = min(floors[k], capacity)
        allocation[k] += add

    used = sum(allocation.values())
    slots = target - used

    remainders = sorted(
        keys,
        key=lambda k: (
            -(raw[k] - int(raw[k])),
            -positive[k],
            k,
        ),
    )

    while slots > 0:
        moved = False
        for k in remainders:
            if allocation[k] < positive[k]:
                allocation[k] += 1
                slots -= 1
                moved = True
                if slots == 0:
                    break
        if not moved:
            break

    return allocation


def pair_quotas(rows: list[dict], target: int) -> dict[tuple[str, str], int]:
    """
    Balanced disagreement-type design.

    Give each of the six off-diagonal Stage1→ML pairs an equal base
    share, then distribute any remainder to the largest populations.
    """
    counts = Counter(
        (text(r.get("stage1_label")).upper(), text(r.get("model_label")).upper())
        for r in rows
    )

    pairs = [pair for pair in PAIR_ORDER if counts[pair] > 0]
    if not pairs:
        return {}

    target = min(target, sum(counts[p] for p in pairs))
    base = target // len(pairs)
    quotas = {p: min(base, counts[p]) for p in pairs}

    remaining = target - sum(quotas.values())

    while remaining > 0:
        candidates = [
            p for p in pairs
            if quotas[p] < counts[p]
        ]
        if not candidates:
            break

        # Fill larger under-sampled strata first.
        candidates.sort(
            key=lambda p: (
                -(counts[p] - quotas[p]),
                PAIR_ORDER.index(p),
            )
        )

        for p in candidates:
            if remaining <= 0:
                break
            quotas[p] += 1
            remaining -= 1

    return quotas


def select_sample(rows: list[dict], target: int, seed: int):
    rng = random.Random(seed)

    by_pair = defaultdict(list)
    for row in rows:
        pair = (
            text(row.get("stage1_label")).upper(),
            text(row.get("model_label")).upper(),
        )
        if pair in PAIR_ORDER:
            by_pair[pair].append(row)

    quotas = pair_quotas(rows, target)
    selected = []
    design = []

    for pair in PAIR_ORDER:
        population = by_pair.get(pair, [])
        quota = quotas.get(pair, 0)
        if not population or quota <= 0:
            continue

        source_counts = Counter(text(r.get("source_name")) or "UNKNOWN" for r in population)
        source_quota = largest_remainder_allocation(dict(source_counts), quota)

        pair_selected = []

        for source in sorted(source_counts):
            candidates = [
                r for r in population
                if (text(r.get("source_name")) or "UNKNOWN") == source
            ]
            rng.shuffle(candidates)
            take = min(source_quota.get(source, 0), len(candidates))
            pair_selected.extend(candidates[:take])

        # Fill any shortfall caused by capacity/rounding.
        selected_keys = {text(r.get("article_key")) for r in pair_selected}
        if len(pair_selected) < quota:
            remainder = [
                r for r in population
                if text(r.get("article_key")) not in selected_keys
            ]
            rng.shuffle(remainder)
            pair_selected.extend(remainder[: quota - len(pair_selected)])

        pair_population = len(population)
        pair_sample = len(pair_selected)
        weight = (
            pair_population / pair_sample
            if pair_sample
            else None
        )

        for row in pair_selected:
            item = dict(row)
            item["evaluation_stratum"] = f"{pair[0]}_TO_{pair[1]}"
            item["stratum_population_n"] = pair_population
            item["stratum_sample_n"] = pair_sample
            item["sampling_weight"] = round(weight, 8) if weight is not None else ""
            selected.append(item)

        design.append({
            "stage1_label": pair[0],
            "model_label": pair[1],
            "stratum_population_n": pair_population,
            "stratum_sample_n": pair_sample,
            "sampling_weight": round(weight, 8) if weight is not None else "",
        })

    rng.shuffle(selected)
    return selected, design


def enrich_with_article_text(rows: list[dict], staged_index: dict[str, dict]):
    enriched = []
    missing = []

    for row in rows:
        source_url = text(row.get("source_url"))
        canonical_url = text(row.get("canonical_url"))

        match = (
            staged_index.get(canonical_url)
            or staged_index.get(source_url)
        )

        item = dict(row)

        if match:
            payload = match["payload"]
            item["article_text"] = text(payload.get("article_text"))
            item["staged_file"] = str(
                match["path"].relative_to(PROJECT_ROOT)
            )
            item["article_text_found"] = bool(item["article_text"])
        else:
            item["article_text"] = ""
            item["staged_file"] = ""
            item["article_text_found"] = False
            missing.append({
                "article_key": text(row.get("article_key")),
                "source_name": text(row.get("source_name")),
                "headline": text(row.get("headline")),
                "source_url": source_url,
                "canonical_url": canonical_url,
            })

        enriched.append(item)

    return enriched, missing


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Build a blind, stratified human-evaluation sample from "
            "the full Stage1-vs-ML disagreement population."
        )
    )
    parser.add_argument(
        "--audit-dir",
        default="",
        help=(
            "Historical ML audit directory containing disagreements_all.csv. "
            "If omitted, the newest audit directory is used."
        ),
    )
    parser.add_argument("--target", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    audit_dir = (
        Path(args.audit_dir)
        if args.audit_dir
        else latest_audit_dir()
    )
    if not audit_dir.is_absolute():
        audit_dir = PROJECT_ROOT / audit_dir

    disagreements_path = audit_dir / "disagreements_all.csv"
    if not disagreements_path.exists():
        raise FileNotFoundError(disagreements_path)

    rows = read_csv(disagreements_path)
    selected, design = select_sample(rows, max(1, args.target), args.seed)

    staged_index = build_staged_index()
    selected, missing = enrich_with_article_text(selected, staged_index)

    out_root = audit_dir / "human_evaluation"
    out_root.mkdir(parents=True, exist_ok=True)

    # Stable review IDs and rank.
    for rank, row in enumerate(selected, 1):
        row["review_rank"] = rank
        row["review_id"] = "hev_" + hashlib.sha256(
            text(row.get("article_key")).encode("utf-8")
        ).hexdigest()[:12]
        row["human_relevance_label"] = ""
        row["review_decision"] = ""
        row["reviewer"] = ""
        row["review_notes"] = ""

    blind_fields = [
        "review_rank",
        "review_id",
        "publication_date",
        "source_name",
        "headline",
        "article_text",
        "source_url",
        "canonical_url",
        "primary_entities",
        "all_entities",
        "regulated_primary_entities",
        "article_text_found",
        "staged_file",
        "human_relevance_label",
        "review_decision",
        "reviewer",
        "review_notes",
    ]

    key_fields = [
        "review_rank",
        "review_id",
        "article_key",
        "evaluation_stratum",
        "stratum_population_n",
        "stratum_sample_n",
        "sampling_weight",
        "stage1_label",
        "stage1_gate_decision",
        "model_label",
        "model_confidence",
        "prob_noise",
        "prob_watch",
        "prob_relevant",
        "review_priority",
        "has_primary_regulated_entity",
        "publication_date",
        "source_name",
        "headline",
        "source_url",
        "canonical_url",
    ]

    write_csv(out_root / "human_evaluation_BLIND_200.csv", selected, blind_fields)
    write_csv(out_root / "human_evaluation_KEY_200.csv", selected, key_fields)
    write_csv(
        out_root / "human_evaluation_sampling_design.csv",
        design,
        [
            "stage1_label",
            "model_label",
            "stratum_population_n",
            "stratum_sample_n",
            "sampling_weight",
        ],
    )
    write_csv(
        out_root / "human_evaluation_missing_article_text.csv",
        missing,
        [
            "article_key",
            "source_name",
            "headline",
            "source_url",
            "canonical_url",
        ],
    )

    summary = {
        "created_at": datetime.now(TZ).isoformat(),
        "audit_dir": str(audit_dir),
        "disagreement_population": len(rows),
        "target": args.target,
        "selected": len(selected),
        "seed": args.seed,
        "article_text_found": sum(bool(r["article_text_found"]) for r in selected),
        "article_text_missing": len(missing),
        "sampling_design": design,
        "method": (
            "Balanced across the six Stage1-to-ML disagreement directions; "
            "within each direction, allocated across sources proportionally "
            "with minimum representation where possible. Sampling weights "
            "permit population-weighted evaluation after adjudication."
        ),
        "blind_review_rule": (
            "Use human_evaluation_BLIND_200.csv for adjudication. "
            "Do not expose Stage1/model labels to the reviewer before the "
            "human label is recorded."
        ),
    }

    (out_root / "human_evaluation_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print("=" * 88)
    print("HUMAN EVALUATION SAMPLE BUILT")
    print("=" * 88)
    print(f"Disagreement population : {len(rows)}")
    print(f"Selected                : {len(selected)}")
    print(f"Article text found      : {summary['article_text_found']}")
    print(f"Article text missing    : {summary['article_text_missing']}")
    print()
    print("SAMPLING DESIGN")
    for item in design:
        print(
            f"  {item['stage1_label']:<8} -> {item['model_label']:<8} "
            f"population={item['stratum_population_n']:<4} "
            f"sample={item['stratum_sample_n']:<3} "
            f"weight={item['sampling_weight']}"
        )
    print()
    print(f"Blind file : {out_root / 'human_evaluation_BLIND_200.csv'}")
    print(f"Key file   : {out_root / 'human_evaluation_KEY_200.csv'}")
    print(f"Summary    : {out_root / 'human_evaluation_summary.json'}")


if __name__ == "__main__":
    main()
