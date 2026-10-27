from __future__ import annotations

from dataclasses import (
    asdict,
    dataclass,
)
from pathlib import Path
import re
import unicodedata

import yaml


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[3]
)

DEFAULT_TAXONOMY_CONFIG = (
    PROJECT_ROOT
    / "config"
    / "intelligence"
    / "taxonomy.yaml"
)


def load_taxonomy_config(
    path: Path | str = DEFAULT_TAXONOMY_CONFIG,
) -> dict:

    payload = yaml.safe_load(
        Path(path).read_text(
            encoding="utf-8-sig"
        )
    )

    if not isinstance(
        payload,
        dict,
    ):
        raise RuntimeError(
            f"Invalid taxonomy configuration: {path}"
        )

    return payload


def _normalize(
    value: str,
) -> str:

    text = unicodedata.normalize(
        "NFKC",
        str(
            value
            or ""
        ),
    ).lower()

    text = re.sub(
        r"[^a-z0-9%]+",
        " ",
        text,
    )

    return re.sub(
        r"\s+",
        " ",
        text,
    ).strip()


def _phrase_count(
    text: str,
    phrase: str,
) -> int:

    text = _normalize(
        text
    )

    phrase = _normalize(
        phrase
    )

    if (
        not text
        or not phrase
    ):
        return 0

    pattern = (
        r"(?<![a-z0-9])"
        + re.escape(
            phrase
        ).replace(
            r"\ ",
            r"\s+",
        )
        + r"(?![a-z0-9])"
    )

    return len(
        re.findall(
            pattern,
            text,
        )
    )


@dataclass(
    frozen=True
)
class SupervisoryTaxonomyAnalysis:

    status: str
    taxonomy_version: str
    provenance_status: str
    primary_topic: str
    topic_labels: tuple[
        str,
        ...
    ]
    topic_scores: dict[
        str,
        float
    ]
    topic_thresholds: dict[
        str,
        float
    ]
    topic_evidence: dict[
        str,
        tuple[
            str,
            ...
        ]
    ]

    def to_dict(
        self,
    ) -> dict:

        return asdict(
            self
        )


class SupervisoryTaxonomyEngine:

    def __init__(
        self,
        *,
        config: dict | None = None,
    ) -> None:

        self.config = (
            config
            or load_taxonomy_config()
        )

        self.version = str(
            self.config.get(
                "version",
                "taxonomy-unknown",
            )
        )

        self.provenance_status = str(
            self.config.get(
                "provenance_status",
                "RULE_ASSISTED_PROVISIONAL",
            )
        )

        self.policy = (
            self.config.get(
                "policy",
                {},
            )
            or {}
        )

        scoring = (
            self.config.get(
                "text_scoring",
                {},
            )
            or {}
        )

        self.lead_characters = int(
            scoring.get(
                "lead_characters",
                1400,
            )
        )

        self.maximum_occurrences = int(
            scoring.get(
                "maximum_occurrences_per_phrase_per_section",
                3,
            )
        )

        self.section_weights = {
            str(key): float(value)
            for key, value in (
                scoring.get(
                    "section_weights",
                    {},
                )
                or {}
            ).items()
        }

        self.phrase_weights = {
            str(key): float(value)
            for key, value in (
                scoring.get(
                    "phrase_weights",
                    {},
                )
                or {}
            ).items()
        }

        self.topics = (
            self.config.get(
                "topics",
                {},
            )
            or {}
        )

        self.default_minimum_topic_score = float(
            self.policy.get(
                "minimum_topic_score",
                4.0,
            )
        )

        self.maximum_topics = int(
            self.policy.get(
                "maximum_topics_per_article",
                3,
            )
        )

        if "OTHER_SUPERVISORY" not in self.topics:

            raise RuntimeError(
                "taxonomy.yaml must include OTHER_SUPERVISORY."
            )


    def _topic_threshold(
        self,
        topic: str,
    ) -> float:

        topic_config = (
            self.topics.get(
                topic,
                {},
            )
            or {}
        )

        return float(
            topic_config.get(
                "minimum_score",
                self.default_minimum_topic_score,
            )
        )


    def _score_topic(
        self,
        *,
        topic: str,
        headline: str,
        lead: str,
        body: str,
    ) -> tuple[
        float,
        tuple[
            str,
            ...
        ],
    ]:

        topic_config = (
            self.topics.get(
                topic,
                {},
            )
            or {}
        )

        score = 0.0
        evidence = []

        sections = {
            "headline": headline,
            "lead": lead,
            "body": body,
        }

        for strength in (
            "strong",
            "normal",
        ):

            phrase_weight = float(
                self.phrase_weights.get(
                    strength,
                    1.0,
                )
            )

            for phrase in (
                topic_config.get(
                    strength,
                    [],
                )
                or []
            ):

                for (
                    section_name,
                    section_text,
                ) in sections.items():

                    count = _phrase_count(
                        section_text,
                        str(
                            phrase
                        ),
                    )

                    if count <= 0:
                        continue

                    used_count = min(
                        count,
                        self.maximum_occurrences,
                    )

                    contribution = (
                        used_count
                        * float(
                            self.section_weights.get(
                                section_name,
                                1.0,
                            )
                        )
                        * phrase_weight
                    )

                    score += contribution

                    evidence.append(
                        (
                            f"{section_name.upper()}:"
                            f"{strength.upper()}:"
                            f"{phrase}:"
                            f"count={used_count}:"
                            f"score={contribution:.2f}"
                        )
                    )

        return (
            float(score),
            tuple(evidence),
        )


    def analyse(
        self,
        *,
        headline: str,
        article_text: str,
    ) -> SupervisoryTaxonomyAnalysis:

        headline = str(
            headline
            or ""
        ).strip()

        article_text = str(
            article_text
            or ""
        ).strip()

        if (
            not headline
            and not article_text
        ):

            raise ValueError(
                "Taxonomy analysis requires headline or article text."
            )

        lead = (
            article_text[
                :self.lead_characters
            ]
        )

        body = (
            article_text[
                self.lead_characters:
            ]
        )

        scores: dict[
            str,
            float
        ] = {}

        thresholds: dict[
            str,
            float
        ] = {}

        evidence: dict[
            str,
            tuple[
                str,
                ...
            ]
        ] = {}

        for topic in (
            self.topics
        ):

            if topic == "OTHER_SUPERVISORY":
                continue

            threshold = (
                self._topic_threshold(
                    topic
                )
            )

            thresholds[
                topic
            ] = (
                threshold
            )

            (
                score,
                topic_evidence,
            ) = (
                self._score_topic(
                    topic=topic,
                    headline=headline,
                    lead=lead,
                    body=body,
                )
            )

            scores[
                topic
            ] = (
                score
            )

            if topic_evidence:

                evidence[
                    topic
                ] = (
                    topic_evidence
                )

        ranked = sorted(
            (
                (
                    topic,
                    score,
                )
                for (
                    topic,
                    score,
                ) in scores.items()
                if score
                >= thresholds[
                    topic
                ]
            ),
            key=lambda item: (
                -item[1],
                item[0],
            ),
        )

        selected = [
            topic
            for (
                topic,
                _,
            ) in ranked[
                :self.maximum_topics
            ]
        ]

        if not selected:

            selected = [
                "OTHER_SUPERVISORY"
            ]

            scores[
                "OTHER_SUPERVISORY"
            ] = 0.0

            thresholds[
                "OTHER_SUPERVISORY"
            ] = 0.0

            evidence[
                "OTHER_SUPERVISORY"
            ] = (
                "FALLBACK:NO_TOPIC_ABOVE_TOPIC_SPECIFIC_THRESHOLD",
            )

        primary = (
            selected[0]
        )

        return (
            SupervisoryTaxonomyAnalysis(
                status="SUCCESS",
                taxonomy_version=(
                    self.version
                ),
                provenance_status=(
                    self.provenance_status
                ),
                primary_topic=(
                    primary
                ),
                topic_labels=tuple(
                    selected
                ),
                topic_scores={
                    topic: float(
                        scores.get(
                            topic,
                            0.0,
                        )
                    )
                    for topic in (
                        selected
                    )
                },
                topic_thresholds={
                    topic: float(
                        thresholds.get(
                            topic,
                            0.0,
                        )
                    )
                    for topic in (
                        selected
                    )
                },
                topic_evidence={
                    topic: tuple(
                        evidence.get(
                            topic,
                            (),
                        )
                    )
                    for topic in (
                        selected
                    )
                },
            )
        )
