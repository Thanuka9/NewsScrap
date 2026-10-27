from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import random
import statistics
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ML_ROOT = PROJECT_ROOT / "data" / "processed" / "ml"
STAGE1_ROOT = PROJECT_ROOT / "data" / "processed" / "stage1"
OUTPUT_ROOT = PROJECT_ROOT / "data" / "review" / "historical_ml_audit"
TZ = ZoneInfo("Asia/Colombo")

STAGE1_TO_LABEL = {
    "INCLUDE_CANDIDATE": "RELEVANT",
    "WATCH": "WATCH",
    "EXCLUDE_NOISE": "NOISE",
}
LABELS = ("NOISE", "WATCH", "RELEVANT")
REGULATED_TYPES = {
    "LICENSED_COMMERCIAL_BANK",
    "LICENSED_SPECIALISED_BANK",
    "REGULATOR",
    "REGULATOR_UNIT",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def text(value) -> str:
    return str(value or "").strip()


def number(value):
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def percent(n: int, d: int) -> float:
    return round(100.0 * n / d, 4) if d else 0.0


def project_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else PROJECT_ROOT / p


def relpath(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields, seen = [], set()
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    fields.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def relevance_payload(record: dict) -> dict:
    ml = record.get("ml") or {}
    for candidate in (
        ml.get("supervisory_relevance"),
        ml.get("relevance"),
        record.get("supervisory_relevance"),
    ):
        if isinstance(candidate, dict):
            return candidate
    return {}


def model_label(rel: dict) -> str:
    for key in ("predicted_label", "label", "prediction", "relevance_label"):
        value = text(rel.get(key)).upper()
        if value:
            return value
    return ""


def probabilities(rel: dict) -> dict:
    raw = rel.get("probabilities") or rel.get("scores") or {}
    if not isinstance(raw, dict):
        return {}
    return {text(k).upper(): number(v) for k, v in raw.items()}


def model_confidence(rel: dict, label: str):
    for key in ("confidence", "predicted_confidence", "score"):
        value = number(rel.get(key))
        if value is not None:
            return value
    return probabilities(rel).get(label)


def entity_info(record: dict) -> dict:
    ml = record.get("ml") or {}
    roles = ml.get("entity_roles") or {}
    primary = [text(x) for x in (roles.get("primary_entities") or []) if text(x)]
    primary_set = set(primary)
    all_names, regulated = [], []

    for item in roles.get("entities") or []:
        if not isinstance(item, dict):
            continue
        name = text(item.get("canonical_name"))
        typ = text(item.get("institution_type")).upper()
        if name:
            all_names.append(name)
        if name in primary_set and typ in REGULATED_TYPES:
            regulated.append(name)

    if not all_names:
        for item in ml.get("cbsl_entity_matches") or []:
            if isinstance(item, dict) and text(item.get("canonical_name")):
                all_names.append(text(item.get("canonical_name")))

    return {
        "primary_entities": "; ".join(dict.fromkeys(primary)),
        "all_entities": "; ".join(dict.fromkeys(all_names)),
        "regulated_primary_entities": "; ".join(dict.fromkeys(regulated)),
        "has_primary_regulated_entity": bool(regulated),
    }


def article_key(row: dict) -> str:
    if row["canonical_url"]:
        return "canonical:" + row["canonical_url"]
    if row["source_url"]:
        return "source:" + row["source_url"]
    raw = "|".join(
        [row["publication_date"], row["source_name"], row["headline"]]
    )
    return "fallback:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def flatten(record: dict, analysis_path: Path, manifest: dict) -> dict:
    s1 = record.get("stage1") or {}
    decision = text(s1.get("gate_decision")).upper()
    s1_label = STAGE1_TO_LABEL.get(decision, "")

    rel = relevance_payload(record)
    m_label = model_label(rel)
    conf = model_confidence(rel, m_label)
    probs = probabilities(rel)

    valid = bool(s1_label) and m_label in LABELS
    agree = valid and s1_label == m_label
    disagree = valid and not agree

    if disagree and {s1_label, m_label} == {"NOISE", "RELEVANT"}:
        priority = "HIGH"
    elif disagree:
        priority = "MEDIUM"
    elif valid:
        priority = "NONE"
    else:
        priority = "INVALID"

    ml = record.get("ml") or {}
    finbert = ml.get("finbert") or {}
    ent = entity_info(record)

    publication_date = (
        text(record.get("publication_date"))
        or text(manifest.get("publication_date"))
    )

    row = {
        "publication_date": publication_date,
        "month": publication_date[:7],
        "source_name": text(record.get("source_name")),
        "headline": text(record.get("headline")),
        "published_at": text(record.get("published_at")),
        "source_url": text(record.get("source_url")),
        "canonical_url": text(record.get("canonical_url")),
        "analysis_file": relpath(analysis_path),
        "embedding_file": text(record.get("embedding_file")),
        "ml_version": text(record.get("ml_version")) or text(manifest.get("ml_version")),
        "stage1_gate_decision": decision,
        "stage1_label": s1_label,
        "stage1_financial_relevance_score": s1.get("financial_relevance_score"),
        "stage1_pr_noise_score": s1.get("pr_noise_score"),
        "model_label": m_label,
        "model_confidence": conf,
        "prob_noise": probs.get("NOISE"),
        "prob_watch": probs.get("WATCH"),
        "prob_relevant": probs.get("RELEVANT"),
        "comparison_status": "AGREE" if agree else ("DISAGREE" if disagree else "INVALID"),
        "review_priority": priority,
        "finbert_sentiment_label": text(finbert.get("sentiment_label")),
        "finbert_sentiment_confidence": number(finbert.get("sentiment_confidence")),
        **ent,
    }
    row["article_key"] = article_key(row)
    return row


ARTICLE_FIELDS = [
    "publication_date", "month", "source_name", "headline", "published_at",
    "source_url", "canonical_url", "article_key", "analysis_file",
    "embedding_file", "ml_version", "stage1_gate_decision", "stage1_label",
    "stage1_financial_relevance_score", "stage1_pr_noise_score",
    "model_label", "model_confidence", "prob_noise", "prob_watch",
    "prob_relevant", "comparison_status", "review_priority",
    "primary_entities", "all_entities", "regulated_primary_entities",
    "has_primary_regulated_entity", "finbert_sentiment_label",
    "finbert_sentiment_confidence",
]


def collect(start: str, end: str):
    rows, issues = [], []
    meta = {
        "manifest_count": 0,
        "manifest_article_total": 0,
        "manifest_versions": Counter(),
        "ml_versions": Counter(),
    }

    for date_root in sorted(ML_ROOT.iterdir() if ML_ROOT.exists() else []):
        if not date_root.is_dir():
            continue
        d = date_root.name
        if not (start <= d <= end):
            continue

        manifest_path = date_root / "ml_manifest.json"
        if not manifest_path.exists():
            issues.append({
                "severity": "ERROR", "publication_date": d,
                "issue": "ML_MANIFEST_MISSING", "path": relpath(manifest_path),
                "detail": "",
            })
            continue

        try:
            manifest = load_json(manifest_path)
        except Exception as exc:
            issues.append({
                "severity": "ERROR", "publication_date": d,
                "issue": "ML_MANIFEST_INVALID_JSON", "path": relpath(manifest_path),
                "detail": f"{type(exc).__name__}: {exc}",
            })
            continue

        meta["manifest_count"] += 1
        meta["manifest_versions"][text(manifest.get("manifest_version")) or "UNKNOWN"] += 1
        meta["ml_versions"][text(manifest.get("ml_version")) or "UNKNOWN"] += 1

        if text(manifest.get("status")).upper() != "SUCCESS":
            issues.append({
                "severity": "ERROR", "publication_date": d,
                "issue": "ML_MANIFEST_NOT_SUCCESS", "path": relpath(manifest_path),
                "detail": text(manifest.get("status")),
            })

        refs = manifest.get("articles") or []
        declared = manifest.get("article_count")
        try:
            declared = int(declared)
        except (TypeError, ValueError):
            declared = len(refs)
        meta["manifest_article_total"] += declared

        if declared != len(refs):
            issues.append({
                "severity": "ERROR", "publication_date": d,
                "issue": "MANIFEST_ARTICLE_COUNT_MISMATCH",
                "path": relpath(manifest_path),
                "detail": f"declared={declared}; listed={len(refs)}",
            })

        for ref in refs:
            analysis_file = text(ref.get("analysis_file")) if isinstance(ref, dict) else ""
            if not analysis_file:
                issues.append({
                    "severity": "ERROR", "publication_date": d,
                    "issue": "ANALYSIS_FILE_NOT_LISTED",
                    "path": relpath(manifest_path), "detail": repr(ref),
                })
                continue

            analysis_path = project_path(analysis_file)
            if not analysis_path.exists():
                issues.append({
                    "severity": "ERROR", "publication_date": d,
                    "issue": "ANALYSIS_FILE_MISSING",
                    "path": analysis_file, "detail": "",
                })
                continue

            try:
                record = load_json(analysis_path)
            except Exception as exc:
                issues.append({
                    "severity": "ERROR", "publication_date": d,
                    "issue": "ANALYSIS_FILE_INVALID_JSON",
                    "path": relpath(analysis_path),
                    "detail": f"{type(exc).__name__}: {exc}",
                })
                continue

            row = flatten(record, analysis_path, manifest)
            rows.append(row)

            if row["publication_date"] != d:
                issues.append({
                    "severity": "ERROR", "publication_date": d,
                    "issue": "ARTICLE_DATE_MANIFEST_MISMATCH",
                    "path": row["analysis_file"],
                    "detail": f"record={row['publication_date']}; manifest={d}",
                })
            if not row["stage1_label"]:
                issues.append({
                    "severity": "ERROR", "publication_date": d,
                    "issue": "STAGE1_LABEL_UNMAPPED",
                    "path": row["analysis_file"], "detail": row["stage1_gate_decision"],
                })
            if row["model_label"] not in LABELS:
                issues.append({
                    "severity": "ERROR", "publication_date": d,
                    "issue": "MODEL_LABEL_MISSING_OR_INVALID",
                    "path": row["analysis_file"], "detail": row["model_label"],
                })
            if (
                row["model_confidence"] is None
                or not 0.0 <= float(row["model_confidence"]) <= 1.0
            ):
                issues.append({
                    "severity": "ERROR", "publication_date": d,
                    "issue": "MODEL_CONFIDENCE_MISSING_OR_INVALID",
                    "path": row["analysis_file"], "detail": repr(row["model_confidence"]),
                })

            emb = row["embedding_file"]
            if not emb:
                issues.append({
                    "severity": "ERROR", "publication_date": d,
                    "issue": "EMBEDDING_FILE_NOT_LISTED",
                    "path": row["analysis_file"], "detail": "",
                })
            elif not project_path(emb).exists():
                issues.append({
                    "severity": "ERROR", "publication_date": d,
                    "issue": "EMBEDDING_FILE_MISSING",
                    "path": emb, "detail": "",
                })

    return rows, issues, meta


def duplicate_checks(rows: list[dict], issues: list[dict]) -> dict:
    summary = {}
    for field, issue_name in (
        ("article_key", "DUPLICATE_ARTICLE_KEY"),
        ("analysis_file", "DUPLICATE_ANALYSIS_FILE"),
        ("source_url", "DUPLICATE_SOURCE_URL"),
    ):
        groups = defaultdict(list)
        for row in rows:
            value = text(row.get(field))
            if value:
                groups[value].append(row)
        dupes = {k: v for k, v in groups.items() if len(v) > 1}
        summary[field] = {
            "duplicate_value_count": len(dupes),
            "duplicate_row_count": sum(len(v) for v in dupes.values()),
        }
        for value, values in dupes.items():
            issues.append({
                "severity": "ERROR",
                "publication_date": ";".join(sorted({x["publication_date"] for x in values})),
                "issue": issue_name, "path": value,
                "detail": f"rows={len(values)}",
            })
    return summary


def stage1_coverage(start: str, end: str) -> dict:
    count, articles = 0, 0
    for date_root in sorted(STAGE1_ROOT.iterdir() if STAGE1_ROOT.exists() else []):
        if not date_root.is_dir() or not (start <= date_root.name <= end):
            continue
        path = date_root / "stage1_manifest.json"
        if not path.exists():
            continue
        try:
            manifest = load_json(path)
        except Exception:
            continue
        if text(manifest.get("status")).upper() != "SUCCESS":
            continue
        count += 1
        try:
            articles += int(manifest.get("article_count") or 0)
        except (TypeError, ValueError):
            pass
    return {"manifest_count": count, "article_total": articles}


def crosstab(rows: list[dict]) -> list[dict]:
    counts = Counter(
        (r["stage1_label"], r["model_label"])
        for r in rows
        if r["stage1_label"] in LABELS and r["model_label"] in LABELS
    )
    result = []
    for s in LABELS:
        result.append({
            "stage1_label": s,
            "model_noise": counts[(s, "NOISE")],
            "model_watch": counts[(s, "WATCH")],
            "model_relevant": counts[(s, "RELEVANT")],
            "row_total": sum(counts[(s, m)] for m in LABELS),
        })
    return result


def grouped(rows: list[dict], field: str) -> list[dict]:
    groups = defaultdict(list)
    for row in rows:
        groups[text(row.get(field)) or "UNKNOWN"].append(row)

    result = []
    for name, values in sorted(groups.items()):
        total = len(values)
        mc = Counter(r["model_label"] for r in values)
        sc = Counter(r["stage1_label"] for r in values)
        agree = sum(r["comparison_status"] == "AGREE" for r in values)
        disagree = sum(r["comparison_status"] == "DISAGREE" for r in values)
        result.append({
            field: name,
            "total_articles": total,
            "model_noise": mc["NOISE"],
            "model_watch": mc["WATCH"],
            "model_relevant": mc["RELEVANT"],
            "stage1_noise": sc["NOISE"],
            "stage1_watch": sc["WATCH"],
            "stage1_relevant": sc["RELEVANT"],
            "agreement_count": agree,
            "agreement_pct": percent(agree, total),
            "disagreement_count": disagree,
            "disagreement_pct": percent(disagree, total),
            "high_priority_disagreements": sum(r["review_priority"] == "HIGH" for r in values),
            "regulated_primary_disagreements": sum(
                r["comparison_status"] == "DISAGREE"
                and bool(r["has_primary_regulated_entity"])
                for r in values
            ),
        })
    return result


def confidence_summary(rows: list[dict]) -> list[dict]:
    result = []
    for label in LABELS:
        values = [
            float(r["model_confidence"])
            for r in rows
            if r["model_label"] == label and r["model_confidence"] is not None
        ]
        values.sort()
        if values:
            result.append({
                "model_label": label,
                "article_count": len(values),
                "mean_confidence": round(statistics.mean(values), 6),
                "median_confidence": round(statistics.median(values), 6),
                "min_confidence": round(values[0], 6),
                "max_confidence": round(values[-1], 6),
            })
        else:
            result.append({
                "model_label": label, "article_count": 0,
                "mean_confidence": None, "median_confidence": None,
                "min_confidence": None, "max_confidence": None,
            })
    return result


def disagreement_key(row: dict):
    priority = {"HIGH": 0, "MEDIUM": 1, "NONE": 2, "INVALID": 3}
    conf = row["model_confidence"]
    return (
        priority.get(row["review_priority"], 9),
        0 if row["has_primary_regulated_entity"] else 1,
        -(float(conf) if conf is not None else -1.0),
        row["publication_date"], row["source_name"], row["headline"],
    )


def review_sample(rows: list[dict], target: int, seed: int) -> list[dict]:
    disagreements = [r for r in rows if r["comparison_status"] == "DISAGREE"]
    strata = defaultdict(list)
    for row in disagreements:
        strata[(row["source_name"], row["stage1_label"], row["model_label"])].append(row)

    rng = random.Random(seed)
    selected, seen = [], set()

    for key in sorted(strata):
        candidates = sorted(strata[key], key=disagreement_key)
        # Deterministic tie diversity.
        if len(candidates) > 1:
            tied = candidates[:]
            rng.shuffle(tied)
            candidates = sorted(tied, key=disagreement_key)
        row = candidates[0]
        if row["article_key"] not in seen:
            selected.append(row)
            seen.add(row["article_key"])
        if len(selected) >= target:
            break

    for row in sorted(disagreements, key=disagreement_key):
        if len(selected) >= target:
            break
        if row["article_key"] in seen:
            continue
        selected.append(row)
        seen.add(row["article_key"])

    output = []
    for rank, row in enumerate(selected, 1):
        item = dict(row)
        item.update({
            "review_rank": rank,
            "human_relevance_label": "",
            "review_decision": "",
            "reviewer": "",
            "review_notes": "",
        })
        output.append(item)
    return output


def main():
    parser = argparse.ArgumentParser(
        description="Corpus-level audit of historical supervisory ML outputs."
    )
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--end-date", default="2026-10-07")
    parser.add_argument("--expected-articles", type=int, default=6556)
    parser.add_argument("--expected-dates", type=int, default=271)
    parser.add_argument("--review-target", type=int, default=200)
    parser.add_argument("--review-seed", type=int, default=42)
    args = parser.parse_args()

    start, end = args.start_date, args.end_date
    datetime.strptime(start, "%Y-%m-%d")
    datetime.strptime(end, "%Y-%m-%d")
    if end < start:
        raise ValueError("--end-date must be >= --start-date")

    stamp = datetime.now(TZ).strftime("%Y%m%d_%H%M%S")
    out = OUTPUT_ROOT / f"{start}_to_{end}_{stamp}"
    out.mkdir(parents=True, exist_ok=True)

    print("=" * 92)
    print("HISTORICAL SUPERVISORY ML CORPUS AUDIT")
    print("=" * 92)

    rows, issues, meta = collect(start, end)
    dupes = duplicate_checks(rows, issues)
    s1 = stage1_coverage(start, end)

    total = len(rows)
    unique_keys = len({r["article_key"] for r in rows})
    unique_urls = len({r["source_url"] for r in rows if r["source_url"]})

    mc = Counter(r["model_label"] for r in rows)
    sc = Counter(r["stage1_label"] for r in rows)
    cc = Counter(r["comparison_status"] for r in rows)
    pc = Counter(r["review_priority"] for r in rows)

    disagreements = sorted(
        [r for r in rows if r["comparison_status"] == "DISAGREE"],
        key=disagreement_key,
    )
    regulated = [r for r in disagreements if r["has_primary_regulated_entity"]]
    n_to_r = [r for r in disagreements if r["stage1_label"] == "NOISE" and r["model_label"] == "RELEVANT"]
    r_to_n = [r for r in disagreements if r["stage1_label"] == "RELEVANT" and r["model_label"] == "NOISE"]

    if args.expected_articles > 0 and unique_keys != args.expected_articles:
        issues.append({
            "severity": "ERROR", "publication_date": "",
            "issue": "EXPECTED_ARTICLE_COUNT_MISMATCH", "path": relpath(ML_ROOT),
            "detail": f"expected={args.expected_articles}; unique={unique_keys}; rows={total}",
        })
    if args.expected_dates > 0 and meta["manifest_count"] != args.expected_dates:
        issues.append({
            "severity": "ERROR", "publication_date": "",
            "issue": "EXPECTED_ML_MANIFEST_COUNT_MISMATCH", "path": relpath(ML_ROOT),
            "detail": f"expected={args.expected_dates}; found={meta['manifest_count']}",
        })
    if s1["article_total"] != total:
        issues.append({
            "severity": "ERROR", "publication_date": "",
            "issue": "STAGE1_ML_ARTICLE_TOTAL_MISMATCH", "path": "",
            "detail": f"stage1={s1['article_total']}; ml={total}",
        })
    if s1["manifest_count"] != meta["manifest_count"]:
        issues.append({
            "severity": "ERROR", "publication_date": "",
            "issue": "STAGE1_ML_DATE_COUNT_MISMATCH", "path": "",
            "detail": f"stage1={s1['manifest_count']}; ml={meta['manifest_count']}",
        })

    sample = review_sample(rows, max(0, args.review_target), args.review_seed)

    write_csv(out / "all_articles.csv", rows, ARTICLE_FIELDS)
    write_csv(out / "stage1_model_crosstab.csv", crosstab(rows))
    write_csv(out / "by_source.csv", grouped(rows, "source_name"))
    write_csv(out / "by_month.csv", grouped(rows, "month"))
    write_csv(out / "confidence_summary.csv", confidence_summary(rows))
    write_csv(out / "disagreements_all.csv", disagreements, ARTICLE_FIELDS)
    write_csv(out / "disagreements_regulated_primary.csv", regulated, ARTICLE_FIELDS)
    write_csv(out / "stage1_noise_model_relevant.csv", n_to_r, ARTICLE_FIELDS)
    write_csv(out / "stage1_relevant_model_noise.csv", r_to_n, ARTICLE_FIELDS)
    write_csv(
        out / "analyst_review_sample.csv",
        sample,
        ["review_rank", *ARTICLE_FIELDS, "human_relevance_label",
         "review_decision", "reviewer", "review_notes"],
    )
    write_csv(
        out / "integrity_issues.csv",
        issues,
        ["severity", "publication_date", "issue", "path", "detail"],
    )

    summary = {
        "audit_version": "historical-ml-corpus-audit-v1",
        "created_at": datetime.now(TZ).isoformat(),
        "start_date": start,
        "end_date": end,
        "coverage": {
            "ml_manifest_count": meta["manifest_count"],
            "ml_manifest_article_total": meta["manifest_article_total"],
            "article_rows_loaded": total,
            "unique_article_keys": unique_keys,
            "unique_source_urls": unique_urls,
            "stage1_manifest_count": s1["manifest_count"],
            "stage1_article_total": s1["article_total"],
            "expected_articles": args.expected_articles,
            "expected_dates": args.expected_dates,
        },
        "versions": {
            "manifest_versions": dict(meta["manifest_versions"]),
            "ml_versions": dict(meta["ml_versions"]),
        },
        "stage1_labels": dict(sc),
        "model_labels": dict(mc),
        "comparison": {
            "agreement_count": cc["AGREE"],
            "agreement_pct": percent(cc["AGREE"], total),
            "disagreement_count": cc["DISAGREE"],
            "disagreement_pct": percent(cc["DISAGREE"], total),
            "invalid_count": cc["INVALID"],
            "high_priority_disagreements": pc["HIGH"],
            "medium_priority_disagreements": pc["MEDIUM"],
            "regulated_primary_disagreements": len(regulated),
            "stage1_noise_model_relevant": len(n_to_r),
            "stage1_relevant_model_noise": len(r_to_n),
        },
        "duplicate_checks": dupes,
        "integrity": {
            "issue_count": len(issues),
            "error_count": sum(x["severity"] == "ERROR" for x in issues),
            "pass": not any(x["severity"] == "ERROR" for x in issues),
        },
        "review_sample_count": len(sample),
        "output_root": relpath(out),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"ML manifests              : {meta['manifest_count']}")
    print(f"ML article rows           : {total}")
    print(f"Unique article keys       : {unique_keys}")
    print(f"Stage-1 declared articles : {s1['article_total']}")
    print()
    print("MODEL LABELS")
    for label in LABELS:
        print(f"  {label:<9}: {mc[label]:>5} ({percent(mc[label], total):>7.3f}%)")
    print()
    print(f"Agreement                 : {cc['AGREE']} ({percent(cc['AGREE'], total):.2f}%)")
    print(f"Disagreement              : {cc['DISAGREE']} ({percent(cc['DISAGREE'], total):.2f}%)")
    print(f"  HIGH priority           : {pc['HIGH']}")
    print(f"  MEDIUM priority         : {pc['MEDIUM']}")
    print(f"Regulated-primary conflicts: {len(regulated)}")
    print(f"Stage1 NOISE -> ML RELEVANT: {len(n_to_r)}")
    print(f"Stage1 RELEVANT -> ML NOISE: {len(r_to_n)}")
    print()
    print(f"Integrity issues          : {len(issues)}")
    print(f"Integrity status          : {'PASS' if summary['integrity']['pass'] else 'FAIL'}")
    print(f"Review sample             : {len(sample)}")
    print(f"Output root               : {out}")
    print(f"Summary                   : {out / 'summary.json'}")


if __name__ == "__main__":
    main()
