from __future__ import annotations

from dataclasses import (
    asdict,
    dataclass,
)

from pathlib import Path

import re

from typing import Iterable

import yaml


# ============================================================
# PROJECT
# ============================================================

PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[3]
)


DEFAULT_REGISTRY = (
    PROJECT_ROOT
    / "config"
    / "intelligence"
    / "entities_cbsl.yaml"
)


# ============================================================
# TEXT NORMALISATION
# ============================================================


def _normalise_text(
    value: str,
) -> str:
    """
    Normalise text for conservative alias matching.

    This is intentionally simple and deterministic.

    The registry is a high-confidence helper layer,
    not our complete NER system.
    """

    value = (
        value
        .replace(
            "’",
            "'",
        )
        .replace(
            "–",
            "-",
        )
        .replace(
            "—",
            "-",
        )
        .lower()
    )

    value = re.sub(
        r"[^a-z0-9]+",
        " ",
        value,
    )

    return (
        re.sub(
            r"\s+",
            " ",
            value,
        )
        .strip()
    )


# ============================================================
# ENTITY RECORD
# ============================================================


@dataclass(
    frozen=True
)
class EntityRecord:

    canonical_name: str

    institution_type: str

    aliases: tuple[
        str,
        ...
    ]

    registry_section: str

    notes: tuple[
        str,
        ...
    ] = ()


# ============================================================
# ENTITY MATCH
# ============================================================


@dataclass(
    frozen=True
)
class EntityMatch:

    canonical_name: str

    institution_type: str

    matched_aliases: tuple[
        str,
        ...
    ]

    registry_section: str

    confidence: float

    def to_dict(
        self,
    ) -> dict:

        return asdict(
            self
        )


# ============================================================
# REGISTRY
# ============================================================


class EntityRegistry:
    """
    High-confidence entity helper.

    Main initial source:
        CBSL licensed-bank lists.

    IMPORTANT
    ---------

    This is NOT a whitelist.

    If an organisation is absent from this registry,
    the article is NOT rejected.

    Unknown organisations remain available for:

        FinBERT
        clustering
        similarity
        later NER
        manual review
        future entity discovery
    """

    def __init__(
        self,
        records: Iterable[
            EntityRecord
        ],
        *,
        version: str,
        as_of: str | None,
    ) -> None:

        self.records = tuple(
            records
        )

        self.version = (
            version
        )

        self.as_of = (
            as_of
        )

        alias_index = []

        for record in (
            self.records
        ):

            seen = set()

            candidates = (
                record.canonical_name,
                *record.aliases,
            )

            for alias in (
                candidates
            ):

                normalised = (
                    _normalise_text(
                        alias
                    )
                )

                if (
                    not normalised
                    or normalised
                    in seen
                ):

                    continue

                seen.add(
                    normalised
                )

                alias_index.append(
                    (
                        normalised,
                        alias,
                        record,
                    )
                )

        # Longer aliases first.
        #
        # Example:
        #
        # "National Development Bank"
        # should be considered before
        # "NDB".

        alias_index.sort(
            key=lambda item: len(
                item[0]
            ),
            reverse=True,
        )

        self._alias_index = tuple(
            alias_index
        )


    # ========================================================
    # LOAD YAML
    # ========================================================

    @classmethod
    def from_yaml(
        cls,
        path: Path | str = (
            DEFAULT_REGISTRY
        ),
    ) -> "EntityRegistry":

        path = Path(
            path
        )

        payload = yaml.safe_load(
            path.read_text(
                encoding="utf-8-sig"
            )
        )

        records = []

        for section in (
            "institutions",
            "additional_entities",
        ):

            items = (
                payload.get(
                    section,
                    [],
                )
                or []
            )

            for item in (
                items
            ):

                records.append(
                    EntityRecord(

                        canonical_name=str(
                            item[
                                "canonical_name"
                            ]
                        ),

                        institution_type=str(
                            item[
                                "institution_type"
                            ]
                        ),

                        aliases=tuple(
                            str(
                                value
                            )
                            for value
                            in (
                                item.get(
                                    "aliases",
                                    [],
                                )
                                or []
                            )
                        ),

                        registry_section=(
                            section
                        ),

                        notes=tuple(
                            str(
                                value
                            )
                            for value
                            in (
                                item.get(
                                    "notes",
                                    [],
                                )
                                or []
                            )
                        ),
                    )
                )

        return cls(
            records,
            version=str(
                payload.get(
                    "version",
                    "unknown",
                )
            ),
            as_of=(
                str(
                    payload[
                        "as_of"
                    ]
                )
                if payload.get(
                    "as_of"
                )
                else None
            ),
        )


    # ========================================================
    # MATCH ARTICLE TEXT
    # ========================================================

    def match(
        self,
        text: str,
    ) -> list[
        EntityMatch
    ]:

        normalised_text = (
            _normalise_text(
                text
            )
        )

        if not normalised_text:

            return []

        padded = (
            f" {normalised_text} "
        )

        matched_by_entity: dict[
            str,
            dict,
        ] = {}

        for (
            normalised_alias,
            original_alias,
            record,
        ) in (
            self._alias_index
        ):

            needle = (
                f" {normalised_alias} "
            )

            if (
                needle
                not in padded
            ):

                continue

            bucket = (
                matched_by_entity
                .setdefault(
                    record.canonical_name,
                    {
                        "record": (
                            record
                        ),
                        "aliases": [],
                    },
                )
            )

            if (
                original_alias
                not in bucket[
                    "aliases"
                ]
            ):

                bucket[
                    "aliases"
                ].append(
                    original_alias
                )


        # ====================================================
        # BUILD MATCH RESULTS
        # ====================================================

        results = []

        for bucket in (
            matched_by_entity
            .values()
        ):

            record = (
                bucket[
                    "record"
                ]
            )

            matched_aliases = tuple(
                bucket[
                    "aliases"
                ]
            )

            # This confidence represents
            # deterministic registry evidence.
            #
            # It is NOT a statistical model probability.

            confidence = (
                1.0
                if any(

                    _normalise_text(
                        alias
                    )
                    == _normalise_text(
                        record
                        .canonical_name
                    )

                    for alias in (
                        matched_aliases
                    )

                )
                else 0.95
            )

            results.append(
                EntityMatch(

                    canonical_name=(
                        record
                        .canonical_name
                    ),

                    institution_type=(
                        record
                        .institution_type
                    ),

                    matched_aliases=(
                        matched_aliases
                    ),

                    registry_section=(
                        record
                        .registry_section
                    ),

                    confidence=(
                        confidence
                    ),
                )
            )

        results.sort(
            key=lambda item: (
                item.institution_type,
                item.canonical_name,
            )
        )

        return results