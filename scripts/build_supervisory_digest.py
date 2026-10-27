from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from bank_intel.intelligence.reporting import (
    DigestArticle,
    all_entities,
    calculate_supervisory_importance,
    display_institution_type,
    display_topic,
    has_primary_regulated_entity,
    load_reporting_config,
    primary_entities,
    primary_entity_details,
    source_summary,
    render_html,
    render_email_html,
    render_qa_html,
    safe_text,
    supervisory_lens,
)
from bank_intel.intelligence.publication_guard import (
    PUBLICATION_POLICY_VERSION,
    build_publication_sections,
    filter_event_groups,
    rescue_high_value_financial_articles,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ML_ROOT = PROJECT_ROOT / "data" / "processed" / "ml"
CORPUS_ROOT = PROJECT_ROOT / "data" / "processed" / "ml_corpus"
TAXONOMY_ROOT = PROJECT_ROOT / "data" / "processed" / "taxonomy"
REPORT_ROOT = PROJECT_ROOT / "data" / "processed" / "reports"
STAGED_ROOT = PROJECT_ROOT / "data" / "staged"
SRI_LANKA_TZ = ZoneInfo("Asia/Colombo")


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def resolve_project_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def relative_project_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def load_csv_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_staged_article_index() -> dict[str, dict]:
    index: dict[str, dict] = {}
    if not STAGED_ROOT.exists():
        return index
    for path in STAGED_ROOT.rglob("*.json"):
        try:
            payload = load_json(path)
        except Exception:
            continue
        source_url = safe_text(payload.get("source_url"))
        canonical_url = safe_text(payload.get("canonical_url"))
        try:
            version = int(payload.get("article_version", 0) or 0)
        except (TypeError, ValueError):
            version = 0
        record = {"path": path, "payload": payload, "version": version}
        for key in {source_url, canonical_url}:
            if not key:
                continue
            previous = index.get(key)
            if previous is None or version >= previous["version"]:
                index[key] = record
    return index


def event_group_maps(payload: dict) -> tuple[dict[str, set[str]], list[dict]]:
    by_url: dict[str, set[str]] = defaultdict(set)
    groups = payload.get("groups", []) or []
    for group in groups:
        group_id = safe_text(group.get("event_group_id"))
        for member in (group.get("members", []) or []):
            url = safe_text(member.get("source_url"))
            if url and group_id:
                by_url[url].add(group_id)
    return by_url, groups


def related_story_counts(rows: list[dict]) -> dict[str, int]:
    neighbors: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        left = safe_text(row.get("left_url"))
        right = safe_text(row.get("right_url"))
        if left and right and left != right:
            neighbors[left].add(right)
            neighbors[right].add(left)
    return {url: len(values) for url, values in neighbors.items()}


def cluster_map(payload: dict) -> dict[str, int | None]:
    result = {}
    for item in (payload.get("articles", []) or []):
        url = safe_text(item.get("source_url"))
        value = item.get("hdbscan_cluster")
        if not url:
            continue
        result[url] = int(value) if value is not None else None
    return result


def phase2_analysis_index(*, end_date: date, days: int) -> dict[str, dict]:
    start_date = end_date - timedelta(days=days - 1)
    current = start_date
    result = {}
    while current <= end_date:
        day = current.isoformat()
        manifest_path = ML_ROOT / day / "ml_manifest.json"
        if not manifest_path.exists():
            current += timedelta(days=1)
            continue
        manifest = load_json(manifest_path)
        if manifest.get("status") != "SUCCESS":
            current += timedelta(days=1)
            continue
        if safe_text(manifest.get("supervisory_relevance_architecture")).upper() != "TWO_STAGE":
            raise RuntimeError(f"Legacy non-two-stage ML output found on {day}.")
        for item in (manifest.get("articles", []) or []):
            analysis_file = str(
                item.get("analysis_file") or ""
            ).strip()

            analysis_path = resolve_project_path(
                analysis_file
            )
            if not analysis_path.exists():
                raise FileNotFoundError(analysis_path)
            analysis = load_json(analysis_path)
            url = safe_text(analysis.get("source_url"))
            if url:
                result[url] = analysis
        current += timedelta(days=1)
    return result


def review_flags_for_article(
    *,
    analysis: dict,
    hdbscan_outlier: bool,
    config: dict,
) -> list[str]:
    flags = config.get("review_flags", {}) or {}
    reasons = []
    comparison = analysis.get("supervisory_comparison", {}) or {}
    stage_a_cmp = comparison.get("stage_a", {}) or {}
    relevance = ((analysis.get("ml", {}) or {}).get("supervisory_relevance", {}) or {})
    stage_a = relevance.get("stage_a", {}) or {}
    stage_b = relevance.get("stage_b", {}) or {}

    if (
        flags.get("include_stage1_stage_a_disagreement", True)
        and stage_a_cmp.get("comparison_status") == "DISAGREE"
    ):
        reasons.append("Stage-1 / Stage-A disagreement")

    if (
        flags.get("include_stage_b_relevant", True)
        and bool(relevance.get("stage_b_executed"))
        and safe_text(stage_b.get("predicted_label")).upper() == "RELEVANT"
    ):
        reasons.append("Stage-B auxiliary RELEVANT")

    if flags.get("include_hdbscan_outlier", True) and hdbscan_outlier:
        reasons.append("HDBSCAN semantic outlier")

    if (
        flags.get("include_phase2_high_priority", True)
        and safe_text(comparison.get("overall_review_priority")).upper() == "HIGH"
    ):
        reasons.append("Phase-2 HIGH review priority")

    if not reasons:
        return []

    return reasons


def title_for_days(days: int, config: dict) -> str:
    titles = config.get("title", {}) or {}
    if days == 1:
        return safe_text(titles.get("daily", "Daily Financial Brief"))
    if days == 7:
        return safe_text(titles.get("weekly", "Weekly Financial Brief"))
    return safe_text(titles.get("rolling", "Financial Brief"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the stakeholder-facing supervisory news intelligence digest."
    )
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=1)
    args = parser.parse_args()

    end_date = parse_date(args.end_date)
    days = int(args.days)
    if days < 1:
        raise ValueError("--days must be >= 1")
    start_date = end_date - timedelta(days=days - 1)

    config = load_reporting_config()
    display = config.get("display", {}) or {}
    sections = config.get("sections", {}) or {}
    core_topics = set(sections.get("core_supervisory_topics", []) or [])
    macro_topics = set(sections.get("macro_market_topics", []) or [])
    other_topics = set(sections.get("other_topics", []) or [])

    corpus_root = CORPUS_ROOT / end_date.isoformat() / f"window_{days}d"
    taxonomy_root = TAXONOMY_ROOT / end_date.isoformat() / f"window_{days}d"

    corpus_manifest_path = corpus_root / "corpus_manifest.json"
    taxonomy_manifest_path = taxonomy_root / "taxonomy_manifest.json"
    article_taxonomy_path = taxonomy_root / "article_taxonomy.json"
    clusters_path = corpus_root / "clusters.json"
    related_pairs_path = corpus_root / "related_pairs.csv"
    event_groups_path = corpus_root / "candidate_event_groups.json"

    required = [
        corpus_manifest_path,
        taxonomy_manifest_path,
        article_taxonomy_path,
        clusters_path,
    ]
    missing = [path for path in required if not path.exists()]
    if missing:
        raise RuntimeError(
            "Required upstream outputs are missing: " + "; ".join(str(path) for path in missing)
        )

    corpus_manifest = load_json(corpus_manifest_path)
    taxonomy_manifest = load_json(taxonomy_manifest_path)
    taxonomy_payload = load_json(article_taxonomy_path)
    clusters_payload = load_json(clusters_path)
    related_rows = load_csv_rows(related_pairs_path)
    event_payload = load_json(event_groups_path) if event_groups_path.exists() else {"groups": []}

    if corpus_manifest.get("status") != "SUCCESS":
        raise RuntimeError("Corpus manifest is not SUCCESS.")
    if taxonomy_manifest.get("status") != "SUCCESS":
        raise RuntimeError("Taxonomy manifest is not SUCCESS.")
    if taxonomy_manifest.get("input_policy") != "STAGE_A_KEEP_ONLY":
        raise RuntimeError("Taxonomy input policy is not Stage-A KEEP only.")

    taxonomy_articles = taxonomy_payload.get("articles", []) or []
    if len(taxonomy_articles) != int(corpus_manifest.get("stage_a_keep_article_count", -1)):
        raise RuntimeError("Taxonomy article count does not reconcile to Stage-A KEEP count.")

    analysis_by_url = phase2_analysis_index(end_date=end_date, days=days)
    staged_index = build_staged_article_index()
    clusters = cluster_map(clusters_payload)
    related_counts = related_story_counts(related_rows)
    event_by_url, event_groups = event_group_maps(event_payload)

    articles: list[DigestArticle] = []
    review_rows = []

    for item in taxonomy_articles:
        url = safe_text(item.get("source_url"))
        analysis = analysis_by_url.get(url)
        if not analysis:
            raise RuntimeError(f"Missing Phase-2 analysis for taxonomy article: {url}")

        canonical_url = safe_text(analysis.get("canonical_url"))
        staged_record = staged_index.get(url) or staged_index.get(canonical_url)
        if not staged_record:
            raise RuntimeError(f"Missing staged article text for digest: {url}")
        staged_payload = staged_record["payload"]

        topics = item.get("topic_labels", [])
        if isinstance(topics, str):
            topics = [value.strip() for value in topics.split(";") if value.strip()]
        topics = [safe_text(value) for value in (topics or []) if safe_text(value)]
        if not topics:
            topics = [safe_text(item.get("primary_topic")) or "OTHER_SUPERVISORY"]

        relevance = ((analysis.get("ml", {}) or {}).get("supervisory_relevance", {}) or {})
        stage_b = relevance.get("stage_b", {}) or {}
        comparison = analysis.get("supervisory_comparison", {}) or {}
        stage_a_cmp = comparison.get("stage_a", {}) or {}

        cluster = clusters.get(url)
        is_outlier = cluster == -1
        event_ids = tuple(sorted(event_by_url.get(url, set())))
        related_count = int(related_counts.get(url, 0))
        entity_details = primary_entity_details(analysis)
        primary_names = tuple(
            safe_text(value.get("canonical_name"))
            for value in entity_details
            if safe_text(value.get("canonical_name"))
        )
        primary_types = tuple(
            display_institution_type(value.get("institution_type"), config)
            for value in entity_details
            if safe_text(value.get("canonical_name"))
        )
        primary_regulated = has_primary_regulated_entity(analysis)

        score, components = calculate_supervisory_importance(
            topics=topics,
            has_primary_regulated=primary_regulated,
            config=config,
        )

        digest = DigestArticle(
            publication_date=safe_text(item.get("publication_date")),
            source_name=safe_text(item.get("source_name")),
            headline=safe_text(item.get("headline")),
            source_url=url,
            news_summary=source_summary(
                safe_text(staged_payload.get("article_text")),
                max_characters=int(display.get("summary_characters", 950)),
                max_sentences=int(display.get("summary_max_sentences", 5)),
            ),
            primary_topic=safe_text(item.get("primary_topic")),
            topic_labels=tuple(topics),
            topic_display_names=tuple(display_topic(topic, config) for topic in topics),
            supervisory_lens=supervisory_lens(topics, config),
            primary_entities=primary_names,
            primary_entity_types=primary_types,
            all_entities=tuple(all_entities(analysis)),
            has_primary_regulated_entity=primary_regulated,
            stage_b_label=safe_text(stage_b.get("predicted_label")),
            stage_b_confidence=(
                float(stage_b.get("confidence"))
                if stage_b.get("confidence") is not None
                else None
            ),
            hdbscan_cluster=cluster,
            hdbscan_outlier=is_outlier,
            related_story_count=related_count,
            event_group_ids=event_ids,
            phase2_review_priority=safe_text(comparison.get("overall_review_priority")),
            stage1_stage_a_status=safe_text(stage_a_cmp.get("comparison_status")),
            supervisory_importance_score=score,
            supervisory_importance_components=components,
        )
        articles.append(digest)

    # Publication safety-net: rescue only high-confidence financial false negatives.
    # Raw ML output is unchanged; rescued items are explicitly audited below.
    rescued_articles = rescue_high_value_financial_articles(
        analysis_by_url=analysis_by_url,
        staged_index=staged_index,
        existing_urls={item.source_url for item in articles},
        clusters=clusters,
        related_counts=related_counts,
        event_by_url=event_by_url,
        config=config,
        display=display,
    )
    rescued_urls = {item.source_url for item in rescued_articles}
    articles.extend(rescued_articles)

    # Review flags intentionally cover ALL Phase-2 articles, including
    # Stage-A NOISE items, so potential false negatives remain visible.
    for url, analysis in analysis_by_url.items():
        relevance = ((analysis.get("ml", {}) or {}).get("supervisory_relevance", {}) or {})
        stage_a = relevance.get("stage_a", {}) or {}
        stage_b = relevance.get("stage_b", {}) or {}
        comparison = analysis.get("supervisory_comparison", {}) or {}
        cluster = clusters.get(url)
        reasons = review_flags_for_article(
            analysis=analysis,
            hdbscan_outlier=(cluster == -1),
            config=config,
        )
        if not reasons:
            continue
        review_rows.append(
            {
                "headline": safe_text(analysis.get("headline")),
                "source_name": safe_text(analysis.get("source_name")),
                "source_url": url,
                "stage_a_label": safe_text(stage_a.get("predicted_label")),
                "stage_b_label": safe_text(stage_b.get("predicted_label")) or "NOT_EXECUTED",
                "phase2_review_priority": safe_text(comparison.get("overall_review_priority")),
                "reasons": reasons,
            }
        )

    articles.sort(
        key=lambda item: (
            -item.supervisory_importance_score,
            item.publication_date,
            item.source_name,
            item.headline,
        )
    )

    (
        priority_articles,
        macro_market_articles,
        other_articles,
        publication_suppressed_articles,
        publication_decisions,
    ) = build_publication_sections(
        articles,
        max_priority=int(display.get("max_priority_items", 20)),
        max_macro=int(display.get("max_macro_market_items", 20)),
        max_other=int(display.get("max_other_items", 20)),
    )

    published_urls = {
        item.source_url
        for item in (
            priority_articles
            + macro_market_articles
            + other_articles
        )
    }
    event_groups = filter_event_groups(
        event_groups,
        published_urls=published_urls,
    )

    review_rows.sort(
        key=lambda row: (
            0 if safe_text(row.get("phase2_review_priority")).upper() == "HIGH" else 1,
            safe_text(row.get("source_name")),
            safe_text(row.get("headline")),
        )
    )
    total_review_flag_count = len(review_rows)
    review_rows = review_rows[: int(display.get("max_review_items", 20))]
    event_groups = event_groups[: int(display.get("max_event_groups", 10))]

    specific_topic_count = sum(
        1 for item in articles if "OTHER_SUPERVISORY" not in item.topic_labels
    )

    metrics = {
        "all_phase2_article_count": int(corpus_manifest.get("all_phase2_article_count", 0)),
        "stage_a_keep_article_count": int(corpus_manifest.get("stage_a_keep_article_count", 0)),
        "stage_a_noise_audit_count": int(corpus_manifest.get("stage_a_noise_audit_count", 0)),
        "specific_topic_article_count": specific_topic_count,
        "review_flag_count": total_review_flag_count,
        "event_group_count": int(corpus_manifest.get("candidate_event_group_count", 0)),
        "stakeholder_published_article_count": (
            len(priority_articles)
            + len(macro_market_articles)
            + len(other_articles)
        ),
        "publication_suppressed_article_count": len(publication_suppressed_articles),
        "publication_rescue_count": len(rescued_urls),
        "publication_policy_version": PUBLICATION_POLICY_VERSION,
    }

    generated_at = datetime.now(SRI_LANKA_TZ).isoformat()
    title = title_for_days(days, config)
    period_label = (
        end_date.isoformat()
        if days == 1
        else f"{start_date.isoformat()} to {end_date.isoformat()}"
    )

    output_root = REPORT_ROOT / end_date.isoformat() / f"window_{days}d"
    output_root.mkdir(parents=True, exist_ok=True)

    brief_name = f"Daily Financial Sector News Brief {end_date.isoformat()}"
    html_path = output_root / f"{brief_name}.html"
    legacy_html_path = html_path
    email_html_path = output_root / f"{brief_name} Email.html"
    qa_html_path = output_root / f"{brief_name} Review.html"
    learning_csv_path = output_root / f"{brief_name} Review.csv"
    json_path = output_root / f"{brief_name}.json"
    csv_path = output_root / f"{brief_name} Articles.csv"
    manifest_path = output_root / "report_manifest.json"

    stakeholder_html = render_html(
        title=title,
        period_label=period_label,
        generated_at=generated_at,
        metrics=metrics,
        priority_articles=priority_articles,
        macro_market_articles=macro_market_articles,
        other_articles=other_articles,
        review_rows=review_rows,
        event_groups=event_groups,
        config=config,
    )
    html_path.write_text(stakeholder_html, encoding="utf-8")
    # Backward-compatible alias for existing workflows/bookmarks.
    legacy_html_path.write_text(stakeholder_html, encoding="utf-8")

    email_html_path.write_text(
        render_email_html(
            title=title,
            period_label=period_label,
            priority_articles=priority_articles,
            macro_market_articles=macro_market_articles,
            other_articles=other_articles,
        ),
        encoding="utf-8",
    )

    qa_html_path.write_text(
        render_qa_html(
            title=title,
            period_label=period_label,
            generated_at=generated_at,
            review_rows=review_rows,
            metrics=metrics,
            config=config,
        ),
        encoding="utf-8",
    )

    # Preserve analyst annotations across rebuilds.
    human_fields = [
        "human_relevance_label",
        "human_topic_label",
        "new_topic_suggestion",
        "human_decision",
        "reviewer",
        "review_notes",
        "approved_for_training",
    ]
    previous_learning = {
        safe_text(row.get("source_url")): row
        for row in load_csv_rows(learning_csv_path)
        if safe_text(row.get("source_url"))
    }
    learning_rows = []
    for row in review_rows:
        previous = previous_learning.get(safe_text(row.get("source_url")), {})
        learning_row = {
            "publication_window": period_label,
            "headline": safe_text(row.get("headline")),
            "source_name": safe_text(row.get("source_name")),
            "source_url": safe_text(row.get("source_url")),
            "stage_a_label": safe_text(row.get("stage_a_label")),
            "stage_b_label": safe_text(row.get("stage_b_label")),
            "phase2_review_priority": safe_text(row.get("phase2_review_priority")),
            "review_reasons": "; ".join(row.get("reasons", []) or []),
        }
        for field in human_fields:
            learning_row[field] = safe_text(previous.get(field))
        learning_rows.append(learning_row)

    write_csv(
        learning_csv_path,
        learning_rows,
        [
            "publication_window",
            "headline",
            "source_name",
            "source_url",
            "stage_a_label",
            "stage_b_label",
            "phase2_review_priority",
            "review_reasons",
            *human_fields,
        ],
    )

    payload = {
        "status": "SUCCESS",
        "generated_at": generated_at,
        "reporting_version": config.get("version"),
        "period": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
            "days": days,
        },
        "metrics": metrics,
        "sections": {
            "priority_supervisory_developments": [item.to_dict() for item in priority_articles],
            "macro_market_monitoring": [item.to_dict() for item in macro_market_articles],
            "other_monitored_developments": [item.to_dict() for item in other_articles],
            "analyst_review_flags": review_rows,
            "candidate_event_groups": event_groups,
        },
        "governance": {
            "main_digest_stage_a_keep_only": True,
            "stage_b_auxiliary_only": True,
            "taxonomy_human_gold": False,
            "hdbscan_minus_one_semantics": "SEMANTIC_OUTLIER_NOT_RELEVANCE_NOISE",
            "stakeholder_publication_policy": PUBLICATION_POLICY_VERSION,
        },
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    csv_rows = []
    for item in articles:
        row = item.to_dict()
        row["topic_labels"] = "; ".join(item.topic_labels)
        row["topic_display_names"] = "; ".join(item.topic_display_names)
        row["primary_entities"] = "; ".join(item.primary_entities)
        row["all_entities"] = "; ".join(item.all_entities)
        row["event_group_ids"] = "; ".join(item.event_group_ids)
        row["primary_entity_types"] = "; ".join(item.primary_entity_types)
        row["supervisory_importance_components"] = "; ".join(item.supervisory_importance_components)
        # Backward-compatible aliases in CSV.
        row["excerpt"] = item.news_summary
        row["display_priority_score"] = item.supervisory_importance_score
        row["display_priority_components"] = "; ".join(item.supervisory_importance_components)
        decision, decision_reason = publication_decisions.get(
            item.source_url,
            ("SUPPRESS", "NOT_CLASSIFIED"),
        )
        row["publication_bucket"] = decision
        row["publication_reason"] = decision_reason
        row["publication_rescue"] = item.source_url in rescued_urls
        row["publication_policy_version"] = PUBLICATION_POLICY_VERSION
        csv_rows.append(row)

    write_csv(
        csv_path,
        csv_rows,
        [
            "publication_date",
            "source_name",
            "headline",
            "source_url",
            "news_summary",
            "excerpt",
            "primary_topic",
            "topic_labels",
            "topic_display_names",
            "supervisory_lens",
            "primary_entities",
            "primary_entity_types",
            "all_entities",
            "has_primary_regulated_entity",
            "stage_b_label",
            "stage_b_confidence",
            "hdbscan_cluster",
            "hdbscan_outlier",
            "related_story_count",
            "event_group_ids",
            "phase2_review_priority",
            "stage1_stage_a_status",
            "publication_bucket",
            "publication_reason",
            "publication_rescue",
            "publication_policy_version",
            "supervisory_importance_score",
            "supervisory_importance_components",
            "display_priority_score",
            "display_priority_components",
        ],
    )

    manifest = {
        "status": "SUCCESS",
        "generated_at": generated_at,
        "reporting_version": config.get("version"),
        "window_start": start_date.isoformat(),
        "window_end": end_date.isoformat(),
        "days": days,
        "metrics": metrics,
        "taxonomy_version": taxonomy_manifest.get("taxonomy_version"),
        "corpus_manifest": relative_project_path(corpus_manifest_path),
        "taxonomy_manifest": relative_project_path(taxonomy_manifest_path),
        "stakeholder_html_report": relative_project_path(html_path),
        "legacy_html_alias": relative_project_path(legacy_html_path),
        "email_html_report": relative_project_path(email_html_path),
        "qa_model_learning_html": relative_project_path(qa_html_path),
        "model_learning_csv": relative_project_path(learning_csv_path),
        "json_report": relative_project_path(json_path),
        "article_csv": relative_project_path(csv_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    section_counts = {
        "priority_supervisory": len(priority_articles),
        "macro_market": len(macro_market_articles),
        "other_monitored": len(other_articles),
    }

    print()
    print("=" * 88)
    print("SUPERVISORY INTELLIGENCE DIGEST COMPLETE")
    print("=" * 88)
    print(f"Period                   : {period_label}")
    print(f"Articles analysed        : {metrics['all_phase2_article_count']}")
    print(f"Retained for intelligence: {metrics['stage_a_keep_article_count']}")
    print(f"Filtered / audit trail   : {metrics['stage_a_noise_audit_count']}")
    print(f"Specific-topic stories   : {metrics['specific_topic_article_count']}")
    print(f"Priority supervisory     : {section_counts['priority_supervisory']}")
    print(f"Publication suppressed   : {len(publication_suppressed_articles)}")
    print(f"Financial rescues        : {len(rescued_urls)}")
    print(f"Macro / market           : {section_counts['macro_market']}")
    print(f"Other monitored          : {section_counts['other_monitored']}")
    print(f"Analyst review flags     : {metrics['review_flag_count']}")
    print(f"Candidate event groups   : {metrics['event_group_count']}")
    print(f"News Brief               : {html_path}")
    print(f"Email Edition            : {email_html_path}")
    print(f"Review Desk              : {qa_html_path}")
    print(f"Review worksheet         : {learning_csv_path}")
    print(f"Legacy HTML alias        : {legacy_html_path}")
    print(f"JSON audit output        : {json_path}")
    print(f"Article CSV              : {csv_path}")
    print(f"Manifest                 : {manifest_path}")


if __name__ == "__main__":
    main()
