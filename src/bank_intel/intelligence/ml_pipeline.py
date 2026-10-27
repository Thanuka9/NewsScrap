from __future__ import annotations

from dataclasses import (
    asdict,
    dataclass,
)

from typing import Any

import numpy as np

from bank_intel.intelligence.entity_registry import (
    EntityRegistry,
)

from bank_intel.intelligence.entity_roles import (
    analyse_entity_roles,
)

from bank_intel.intelligence.finbert_engine import (
    FinBertEngine,
    load_ml_config,
)

from bank_intel.intelligence.relevance_engine import (
    SupervisoryRelevanceEngine,
)


@dataclass(
    frozen=True
)
class ArticleMLResult:

    entity_registry_version: str

    cbsl_entity_matches: list[
        dict
    ]

    entity_roles: dict

    finbert: dict

    supervisory_relevance: dict

    def to_dict(
        self,
    ) -> dict:

        return asdict(
            self
        )


class ArticleMLPipeline:
    """
    Article-level Phase-2 ML enrichment.

    Production relevance architecture:

        Stage A:
            NOISE vs KEEP
            operational relevance filter

        Stage B:
            WATCH vs RELEVANT
            auxiliary review/ranking signal only
            executed only when Stage A predicts KEEP

    Base ProsusAI/finbert remains unchanged and is used for:
        - financial sentiment
        - finance-domain embeddings

    Stage-1 remains an independent rule-based signal.
    Stage B never overrides the Stage-A operational filter.
    """

    def __init__(
        self,
        *,
        entity_registry: (
            EntityRegistry
            | None
        ) = None,
        finbert_engine: (
            FinBertEngine
            | None
        ) = None,
        stage_a_engine: (
            SupervisoryRelevanceEngine
            | None
        ) = None,
        stage_b_engine: (
            SupervisoryRelevanceEngine
            | None
        ) = None,
        config: (
            dict
            | None
        ) = None,
        lead_characters: int = 1200,
        primary_min_score: float = 5.0,
    ) -> None:

        self.config = (
            config
            or load_ml_config()
        )

        relevance = (
            self.config.get(
                "supervisory_relevance",
                {},
            )
            or {}
        )

        architecture = str(
            relevance.get(
                "architecture",
                "",
            )
            or ""
        ).strip().upper()

        if architecture != "TWO_STAGE":

            raise RuntimeError(
                "Production ml.yaml must configure "
                "supervisory_relevance.architecture=TWO_STAGE."
            )

        stage_a_config = (
            relevance.get(
                "stage_a",
                {},
            )
            or {}
        )

        stage_b_config = (
            relevance.get(
                "stage_b",
                {},
            )
            or {}
        )

        if not stage_a_config:

            raise RuntimeError(
                "Missing supervisory_relevance.stage_a configuration."
            )

        if not stage_b_config:

            raise RuntimeError(
                "Missing supervisory_relevance.stage_b configuration."
            )

        self.entity_registry = (
            entity_registry
            or EntityRegistry.from_yaml()
        )

        self.finbert_engine = (
            finbert_engine
            or FinBertEngine()
        )

        self.stage_a_engine = (
            stage_a_engine
            or SupervisoryRelevanceEngine(
                config=self._engine_config(
                    stage_a_config
                )
            )
        )

        self.stage_b_engine = (
            stage_b_engine
            or SupervisoryRelevanceEngine(
                config=self._engine_config(
                    stage_b_config
                )
            )
        )

        self.lead_characters = int(
            lead_characters
        )

        self.primary_min_score = float(
            primary_min_score
        )


    def _engine_config(
        self,
        stage_config: dict,
    ) -> dict:
        """
        SupervisoryRelevanceEngine intentionally remains a
        single-checkpoint inference engine.

        Give each frozen stage its own engine-specific view of
        the shared production configuration.
        """

        config = dict(
            self.config
        )

        config[
            "supervisory_relevance"
        ] = dict(
            stage_config
        )

        return config


    def validate_required_models(
        self,
        *,
        load_model: bool = False,
    ) -> dict:
        """
        Preflight BOTH frozen relevance checkpoints before any
        ML output directory is rebuilt.
        """

        stage_a = (
            self.stage_a_engine
            .validate_configuration(
                load_model=(
                    load_model
                )
            )
        )

        stage_b = (
            self.stage_b_engine
            .validate_configuration(
                load_model=(
                    load_model
                )
            )
        )

        return {
            "status": "SUCCESS",
            "architecture": "TWO_STAGE",
            "stage_a": stage_a,
            "stage_b": stage_b,
            "policy": {
                "stage_a_operational_filter": True,
                "stage_b_run_only_on_keep": True,
                "stage_b_auxiliary_only": True,
                "stage_b_automatic_gate": False,
            },
        }


    def _skipped_stage_b_payload(
        self,
        *,
        reason: str,
    ) -> dict:

        metadata = (
            self.stage_b_engine
            .metadata()
        )

        return {
            "enabled": (
                metadata.get(
                    "enabled"
                )
            ),
            "status": (
                reason
            ),
            "model_id": (
                metadata.get(
                    "model_id"
                )
            ),
            "model_path": (
                metadata.get(
                    "model_path"
                )
            ),
            "predicted_label": None,
            "confidence": None,
            "probabilities": {},
            "headline_chunks": 0,
            "body_chunks": 0,
            "max_body_chunks": (
                metadata.get(
                    "max_body_chunks"
                )
            ),
            "device": (
                metadata.get(
                    "device"
                )
            ),
            "provenance_status": (
                metadata.get(
                    "provenance_status"
                )
            ),
            "production_role": (
                metadata.get(
                    "production_role"
                )
            ),
        }


    def analyse(
        self,
        *,
        headline: str,
        article_text: str,
    ) -> tuple[
        ArticleMLResult,
        np.ndarray,
    ]:

        combined_text = (
            f"{headline}\n\n"
            f"{article_text}"
        )


        # ====================================================
        # HIGH-CONFIDENCE REGISTRY MATCHES
        # ====================================================

        entity_matches = (
            self.entity_registry
            .match(
                combined_text
            )
        )


        # ====================================================
        # ENTITY PROMINENCE / ROLES
        # ====================================================

        entity_role_analysis = (
            analyse_entity_roles(
                registry=(
                    self.entity_registry
                ),
                headline=(
                    headline
                ),
                article_text=(
                    article_text
                ),
                lead_characters=(
                    self.lead_characters
                ),
                primary_min_score=(
                    self.primary_min_score
                ),
            )
        )


        # ====================================================
        # BASE FINBERT
        # ====================================================

        (
            finbert_analysis,
            embedding,
        ) = (
            self.finbert_engine
            .analyse(
                headline=(
                    headline
                ),
                article_text=(
                    article_text
                ),
            )
        )


        # ====================================================
        # STAGE A — NOISE vs KEEP
        # ====================================================

        stage_a = (
            self.stage_a_engine
            .analyse(
                headline=(
                    headline
                ),
                article_text=(
                    article_text
                ),
            )
        )

        stage_a_payload = (
            stage_a.to_dict()
        )

        stage_a_label = str(
            stage_a.predicted_label
            or ""
        ).strip().upper()

        if stage_a.status != "SUCCESS":

            raise RuntimeError(
                "Stage A relevance inference did not succeed: "
                f"{stage_a.status}"
            )

        if stage_a_label not in {
            "NOISE",
            "KEEP",
        }:

            raise RuntimeError(
                "Stage A returned unexpected label: "
                f"{stage_a_label!r}"
            )

        operational_keep = (
            stage_a_label
            == "KEEP"
        )


        # ====================================================
        # STAGE B — WATCH vs RELEVANT
        #
        # Run only on Stage-A KEEP.
        # This is NEVER an automatic discard/promote gate.
        # ====================================================

        if operational_keep:

            stage_b = (
                self.stage_b_engine
                .analyse(
                    headline=(
                        headline
                    ),
                    article_text=(
                        article_text
                    ),
                )
            )

            stage_b_payload = (
                stage_b.to_dict()
            )

            stage_b_executed = True

            if stage_b.status != "SUCCESS":

                raise RuntimeError(
                    "Stage B relevance inference did not succeed: "
                    f"{stage_b.status}"
                )

            stage_b_label = str(
                stage_b.predicted_label
                or ""
            ).strip().upper()

            if stage_b_label not in {
                "WATCH",
                "RELEVANT",
            }:

                raise RuntimeError(
                    "Stage B returned unexpected label: "
                    f"{stage_b_label!r}"
                )

        else:

            stage_b_payload = (
                self._skipped_stage_b_payload(
                    reason=(
                        "SKIPPED_STAGE_A_NOISE"
                    )
                )
            )

            stage_b_executed = False


        # ====================================================
        # TWO-STAGE RELEVANCE PAYLOAD
        # ====================================================

        supervisory_relevance = {
            "architecture": "TWO_STAGE",

            "stage_a": (
                stage_a_payload
            ),

            "operational_filter_label": (
                stage_a_label
            ),

            "operational_keep": (
                operational_keep
            ),

            "operational_filter_action": (
                "KEEP"
                if operational_keep
                else "FILTER_NOISE"
            ),

            "stage_b_executed": (
                stage_b_executed
            ),

            "stage_b": (
                stage_b_payload
            ),

            "stage_b_policy": {
                "role": (
                    "AUXILIARY_REVIEW_SIGNAL"
                ),
                "automatic_gate": False,
                "may_override_stage_a": False,
                "may_auto_discard": False,
                "may_auto_promote": False,
            },
        }


        # ====================================================
        # RESULT
        # ====================================================

        result = (
            ArticleMLResult(
                entity_registry_version=(
                    self.entity_registry
                    .version
                ),

                cbsl_entity_matches=[
                    item.to_dict()

                    for item in (
                        entity_matches
                    )
                ],

                entity_roles=(
                    entity_role_analysis
                    .to_dict()
                ),

                finbert=(
                    finbert_analysis
                    .to_dict()
                ),

                supervisory_relevance=(
                    supervisory_relevance
                ),
            )
        )

        return (
            result,
            embedding,
        )
