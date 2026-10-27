from __future__ import annotations

from dataclasses import asdict, dataclass

from bank_intel.intelligence.entity_registry import EntityRegistry


@dataclass(frozen=True)
class EntityRoleResult:
    canonical_name: str
    institution_type: str
    roles: tuple[str, ...]
    prominence_score: float
    headline_match: bool
    lead_match: bool
    body_match: bool
    matched_aliases: tuple[str, ...]
    registry_section: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class EntityRoleAnalysis:
    primary_entities: tuple[str, ...]
    entities: tuple[EntityRoleResult, ...]

    def to_dict(self) -> dict:
        return {
            "primary_entities": list(self.primary_entities),
            "entities": [
                item.to_dict()
                for item in self.entities
            ],
        }


def _type_roles(
    institution_type: str,
) -> list[str]:

    roles: list[str] = []

    if institution_type in {
        "LICENSED_COMMERCIAL_BANK",
        "LICENSED_SPECIALISED_BANK",
    }:

        roles.append(
            "LICENSED_BANK_MENTION"
        )

    if institution_type in {
        "REGULATOR",
        "REGULATOR_UNIT",
    }:

        roles.append(
            "REGULATOR_MENTION"
        )

    if (
        institution_type
        == "MARKET_INFRASTRUCTURE"
    ):

        roles.append(
            "MARKET_INFRASTRUCTURE_MENTION"
        )

    if (
        institution_type
        == "PAYMENT_INFRASTRUCTURE"
    ):

        roles.append(
            "PAYMENT_INFRASTRUCTURE_MENTION"
        )

    return roles


def analyse_entity_roles(
    *,
    registry: EntityRegistry,
    headline: str,
    article_text: str,
    lead_characters: int = 1200,
    headline_weight: float = 5.0,
    lead_weight: float = 3.0,
    body_weight: float = 1.0,
    primary_min_score: float = 5.0,
) -> EntityRoleAnalysis:
    """
    Distinguish important entity mentions from incidental
    body mentions.

    HEADLINE_ENTITY
        Entity is mentioned in the headline.

    LEAD_ENTITY
        Entity appears in the first lead_characters
        characters of the article.

    BODY_MENTION
        Entity appears anywhere in the article body.

    PRIMARY_ENTITY
        Entity has the highest prominence score and meets
        primary_min_score.

        Ties are deliberately preserved.

    The high primary threshold is intentional.

    A bank mentioned only inside the article body or a
    director biography should not automatically become the
    primary subject of the article.
    """

    headline = str(
        headline
        or ""
    ).strip()

    article_text = str(
        article_text
        or ""
    ).strip()

    lead_text = article_text[
        :max(
            0,
            int(
                lead_characters
            ),
        )
    ]

    regions = {

        "headline": (
            registry.match(
                headline
            )
        ),

        "lead": (
            registry.match(
                lead_text
            )
        ),

        "body": (
            registry.match(
                article_text
            )
        ),
    }

    by_name: dict[
        str,
        dict,
    ] = {}

    for (
        region,
        matches,
    ) in regions.items():

        for match in matches:

            item = (
                by_name.setdefault(
                    match.canonical_name,
                    {
                        "canonical_name": (
                            match.canonical_name
                        ),

                        "institution_type": (
                            match.institution_type
                        ),

                        "registry_section": (
                            match.registry_section
                        ),

                        "headline": False,

                        "lead": False,

                        "body": False,

                        "aliases": [],
                    },
                )
            )

            item[
                region
            ] = True

            for alias in (
                match.matched_aliases
            ):

                if (
                    alias
                    not in item[
                        "aliases"
                    ]
                ):

                    item[
                        "aliases"
                    ].append(
                        alias
                    )

    scored: list[
        dict
    ] = []

    for item in (
        by_name.values()
    ):

        score = 0.0

        roles: list[
            str
        ] = []

        if item[
            "headline"
        ]:

            score += float(
                headline_weight
            )

            roles.append(
                "HEADLINE_ENTITY"
            )

        if item[
            "lead"
        ]:

            score += float(
                lead_weight
            )

            roles.append(
                "LEAD_ENTITY"
            )

        if item[
            "body"
        ]:

            score += float(
                body_weight
            )

            roles.append(
                "BODY_MENTION"
            )

        roles.extend(
            _type_roles(
                item[
                    "institution_type"
                ]
            )
        )

        scored.append(
            {
                **item,

                "score": (
                    score
                ),

                "roles": (
                    roles
                ),
            }
        )

    max_score = max(
        (
            item[
                "score"
            ]

            for item in (
                scored
            )
        ),
        default=0.0,
    )

    primary_entities: list[
        str
    ] = []

    if (
        max_score
        >= float(
            primary_min_score
        )
    ):

        for item in (
            scored
        ):

            if (
                item[
                    "score"
                ]
                == max_score
            ):

                item[
                    "roles"
                ].insert(
                    0,
                    "PRIMARY_ENTITY",
                )

                primary_entities.append(
                    item[
                        "canonical_name"
                    ]
                )

    results = [

        EntityRoleResult(

            canonical_name=(
                item[
                    "canonical_name"
                ]
            ),

            institution_type=(
                item[
                    "institution_type"
                ]
            ),

            roles=tuple(
                dict.fromkeys(
                    item[
                        "roles"
                    ]
                )
            ),

            prominence_score=float(
                item[
                    "score"
                ]
            ),

            headline_match=bool(
                item[
                    "headline"
                ]
            ),

            lead_match=bool(
                item[
                    "lead"
                ]
            ),

            body_match=bool(
                item[
                    "body"
                ]
            ),

            matched_aliases=tuple(
                item[
                    "aliases"
                ]
            ),

            registry_section=(
                item[
                    "registry_section"
                ]
            ),
        )

        for item in (
            scored
        )
    ]

    results.sort(
        key=lambda item: (

            -item.prominence_score,

            item.institution_type,

            item.canonical_name,
        )
    )

    return (
        EntityRoleAnalysis(

            primary_entities=tuple(
                primary_entities
            ),

            entities=tuple(
                results
            ),
        )
    )