from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import re
from typing import Any
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "intelligence" / "stage1.yaml"

@dataclass
class Stage1Result:
    rules_version: str
    financial_relevance_score: int
    financial_relevance_label: str
    pr_noise_score: int
    pr_noise_label: str
    gate_decision: str
    article_type_hint: str
    institutions: list[str]
    matched_financial_terms: dict[str, list[str]]
    matched_pr_terms: dict[str, list[str]]
    pr_counter_signals: list[str]
    reasons: list[str]
    def to_dict(self) -> dict:
        return asdict(self)

def _normalize(value: str | None) -> str:
    if not value:
        return ""
    value = value.lower().replace("’", "'").replace("‘", "'").replace("–", "-").replace("—", "-")
    value = re.sub(r"[^a-z0-9%+\-'. ]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()

def _contains_term(text: str, term: str) -> bool:
    normalized_term = _normalize(term)
    if not normalized_term:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(normalized_term).replace(r"\ ", r"\s+") + r"(?![a-z0-9])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None

def _match_terms(text: str, terms: list[str]) -> list[str]:
    return [term for term in terms if _contains_term(text, term)]

def load_stage1_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path if path is not None else DEFAULT_CONFIG
    if not config_path.exists():
        raise FileNotFoundError(f"Stage-1 intelligence configuration does not exist:\n{config_path}")
    with config_path.open("r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    if not isinstance(config, dict):
        raise ValueError("stage1.yaml is not a valid configuration mapping.")
    return config

def _score_term_groups(*, headline: str, body: str, groups: dict, headline_multiplier: float) -> tuple[float, dict[str, list[str]]]:
    total_score = 0.0
    matched_by_group: dict[str, list[str]] = {}
    for group_name, group_config in groups.items():
        terms = group_config.get("terms", [])
        weight = float(group_config.get("weight", 0))
        max_score = float(group_config.get("max_score", 100))
        headline_matches = set(_match_terms(headline, terms))
        body_matches = set(_match_terms(body, terms))
        all_matches = sorted(headline_matches | body_matches)
        if not all_matches:
            continue
        group_score = 0.0
        for term in all_matches:
            group_score += weight * headline_multiplier if term in headline_matches else weight
        total_score += min(group_score, max_score)
        matched_by_group[group_name] = all_matches
    return total_score, matched_by_group

def _detect_institutions(*, headline: str, body: str, institutions_config: dict) -> list[str]:
    combined = headline + " " + body
    matches = []
    for canonical_name, data in institutions_config.items():
        if any(_contains_term(combined, alias) for alias in data.get("aliases", [])):
            matches.append(canonical_name)
    return sorted(set(matches))

def _financial_label(score: int) -> str:
    if score >= 60: return "HIGH"
    if score >= 35: return "MEDIUM"
    return "LOW"

def _pr_label(score: int) -> str:
    if score >= 70: return "LIKELY_PR"
    if score >= 40: return "POSSIBLE_PR"
    return "LOW_PR_SIGNAL"

def _article_type_hint(*, financial_score: int, pr_score: int, supervisory_matches: dict, counter_signals: list[str]) -> str:
    if (supervisory_matches or counter_signals) and financial_score >= 50:
        return "SUPERVISORY_NEWS_CANDIDATE"
    if pr_score >= 70: return "LIKELY_CORPORATE_PR"
    if pr_score >= 40: return "POSSIBLE_CORPORATE_PR"
    if financial_score >= 60: return "FINANCIAL_NEWS"
    if financial_score >= 35: return "GENERAL_FINANCIAL"
    return "GENERAL_BUSINESS_OR_NOISE"

def analyse_stage1(*, headline: str, article_text: str, config: dict | None = None) -> Stage1Result:
    if config is None:
        config = load_stage1_config()
    headline_norm = _normalize(headline)
    body_norm = _normalize(article_text)
    financial_raw, matched_financial = _score_term_groups(headline=headline_norm, body=body_norm, groups=config["financial_relevance"], headline_multiplier=1.5)
    institutions = _detect_institutions(headline=headline_norm, body=body_norm, institutions_config=config.get("institutions", {}))
    if institutions:
        financial_raw += min(18 + 4 * (len(institutions) - 1), 30)
    financial_score = min(int(round(financial_raw)), 100)
    pr_raw, matched_pr = _score_term_groups(headline=headline_norm, body=body_norm, groups=config.get("pr_noise", {}), headline_multiplier=1.6)
    counter_signals = _match_terms(headline_norm + " " + body_norm, config.get("pr_counter_signals", []))
    if counter_signals:
        pr_raw -= min(30, 12 + 4 * len(counter_signals))
    if financial_score >= 70:
        pr_raw -= 8
    pr_score = max(0, min(int(round(pr_raw)), 100))
    financial_label = _financial_label(financial_score)
    pr_label = _pr_label(pr_score)
    strong_supervisory_groups = {key: value for key, value in matched_financial.items() if key in {"supervisory_risk", "prudential_regulatory", "governance"}}
    if financial_score >= 60:
        gate_decision = "WATCH" if pr_score >= 75 and not counter_signals and not strong_supervisory_groups else "INCLUDE_CANDIDATE"
    elif financial_score >= 35:
        gate_decision = "WATCH"
    else:
        gate_decision = "EXCLUDE_NOISE"
    article_type = _article_type_hint(financial_score=financial_score, pr_score=pr_score, supervisory_matches=strong_supervisory_groups, counter_signals=counter_signals)
    reasons = []
    if institutions:
        reasons.append("Financial institution detected: " + ", ".join(institutions))
    for group_name, terms in matched_financial.items():
        reasons.append(f"Financial group '{group_name}': " + ", ".join(terms))
    for group_name, terms in matched_pr.items():
        reasons.append(f"PR/noise group '{group_name}': " + ", ".join(terms))
    if counter_signals:
        reasons.append("PR counter-signals: " + ", ".join(counter_signals))
    if not reasons:
        reasons.append("No significant financial, supervisory, institution, or promotional signals detected.")
    return Stage1Result(
        rules_version=str(config.get("version", "UNKNOWN")),
        financial_relevance_score=financial_score,
        financial_relevance_label=financial_label,
        pr_noise_score=pr_score,
        pr_noise_label=pr_label,
        gate_decision=gate_decision,
        article_type_hint=article_type,
        institutions=institutions,
        matched_financial_terms=matched_financial,
        matched_pr_terms=matched_pr,
        pr_counter_signals=counter_signals,
        reasons=reasons,
    )
