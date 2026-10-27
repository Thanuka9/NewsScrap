from __future__ import annotations

from dataclasses import (
    asdict,
    dataclass,
)

from pathlib import Path

from typing import Any

from bank_intel.intelligence.finbert_engine import (
    load_ml_config,
)


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[3]
)


@dataclass(
    frozen=True
)
class SupervisoryRelevanceAnalysis:
    """
    Output from the fine-tuned supervisory relevance model.

    Important:
        This is an auxiliary / review signal.

        It does NOT replace Stage-1 and is not treated as
        human-gold supervisory truth.
    """

    enabled: bool

    status: str

    model_id: str

    model_path: str

    predicted_label: (
        str
        | None
    )

    confidence: (
        float
        | None
    )

    probabilities: dict[
        str,
        float,
    ]

    headline_chunks: int

    body_chunks: int

    max_body_chunks: int

    device: (
        str
        | None
    )

    provenance_status: str

    production_role: str

    def to_dict(
        self,
    ) -> dict:

        return asdict(
            self
        )


class SupervisoryRelevanceEngine:
    """
    Fine-tuned FinBERT classifier for:

        NOISE
        WATCH
        RELEVANT

    This engine is deliberately separate from FinBertEngine.

    FinBertEngine remains responsible for:
        - base ProsusAI/finbert sentiment
        - finance-domain embeddings

    This engine is responsible only for the experimental /
    provisional supervisory relevance signal.
    """

    def __init__(
        self,
        *,
        config: (
            dict
            | None
        ) = None,
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

        self.enabled = bool(
            relevance.get(
                "enabled",
                False,
            )
        )

        self.required = bool(
            relevance.get(
                "required",
                self.enabled,
            )
        )

        self.model_id = str(
            relevance.get(
                "model_id",
                "supervisory-relevance",
            )
        )

        self.model_path_value = str(
            relevance.get(
                "model_path",
                "",
            )
            or ""
        ).strip()

        self.tokenizer_name = str(
            relevance.get(
                "tokenizer_name",
                "ProsusAI/finbert",
            )
        ).strip()

        self.label_order = [
            str(
                value
            )
            .strip()
            .upper()

            for value in (
                relevance.get(
                    "label_order",
                    [
                        "NOISE",
                        "WATCH",
                        "RELEVANT",
                    ],
                )
                or []
            )
        ]

        self.max_length = int(
            relevance.get(
                "max_length",
                512,
            )
        )

        self.stride = int(
            relevance.get(
                "stride",
                96,
            )
        )

        self.batch_size = int(
            relevance.get(
                "batch_size",
                8,
            )
        )

        self.headline_weight = float(
            relevance.get(
                "headline_weight",
                0.35,
            )
        )

        self.body_weight = float(
            relevance.get(
                "body_weight",
                0.65,
            )
        )

        self.max_body_chunks = int(
            relevance.get(
                "max_body_chunks",
                5,
            )
        )

        self.chunk_weighting = str(
            relevance.get(
                "chunk_weighting",
                "token_count",
            )
        ).strip().lower()

        self.device_setting = str(
            relevance.get(
                "device",
                "auto",
            )
        )

        self.provenance_status = str(
            relevance.get(
                "provenance_status",
                "AI_ASSISTED_PROVISIONAL",
            )
        )

        self.production_role = str(
            relevance.get(
                "production_role",
                "AUXILIARY_REVIEW_SIGNAL",
            )
        )

        self.experiment_name = str(
            relevance.get(
                "experiment_name",
                "",
            )
            or ""
        )

        self.expected_best_epoch = (
            relevance.get(
                "expected_best_epoch"
            )
        )

        self.expected_validation_macro_f1 = (
            relevance.get(
                "expected_validation_macro_f1"
            )
        )

        total_weight = (
            self.headline_weight
            + self.body_weight
        )

        if total_weight <= 0:

            raise ValueError(
                "Supervisory relevance headline/body "
                "weights must have a positive total."
            )

        self.headline_weight /= (
            total_weight
        )

        self.body_weight /= (
            total_weight
        )

        if self.max_length < 8:

            raise ValueError(
                "supervisory_relevance.max_length "
                "must be >= 8."
            )

        if self.stride < 0:

            raise ValueError(
                "supervisory_relevance.stride "
                "must be >= 0."
            )

        if self.batch_size < 1:

            raise ValueError(
                "supervisory_relevance.batch_size "
                "must be >= 1."
            )

        if self.max_body_chunks < 1:

            raise ValueError(
                "supervisory_relevance.max_body_chunks "
                "must be >= 1."
            )

        if self.chunk_weighting not in {
            "token_count",
            "uniform",
        }:

            raise ValueError(
                "supervisory_relevance.chunk_weighting "
                "must be 'token_count' or 'uniform'."
            )

        if len(
            self.label_order
        ) < 2:

            raise ValueError(
                "supervisory_relevance.label_order "
                "must contain at least two labels."
            )

        if len(
            set(
                self.label_order
            )
        ) != len(
            self.label_order
        ):

            raise ValueError(
                "supervisory_relevance.label_order "
                "contains duplicate labels."
            )

        self._torch = None

        self._tokenizer = None

        self._model = None

        self._device = None

        self._label_by_index = None


    @property
    def model_path(
        self,
    ) -> Path:

        value = (
            self.model_path_value
        )

        path = Path(
            value
        )

        if not path.is_absolute():

            path = (
                PROJECT_ROOT
                / path
            )

        return path


    def _model_weight_exists(
        self,
        path: Path,
    ) -> bool:

        candidates = (
            "model.safetensors",
            "model.safetensors.index.json",
            "pytorch_model.bin",
            "pytorch_model.bin.index.json",
        )

        return any(
            (
                path
                / name
            ).exists()

            for name in candidates
        )


    def validate_configuration(
        self,
        *,
        load_model: bool = False,
    ) -> dict:
        """
        Fail loudly when the configured fine-tuned checkpoint is
        missing or malformed.

        This prevents an historical ML run from silently falling
        back to base FinBERT only.
        """

        if not self.enabled:

            if self.required:

                raise RuntimeError(
                    "Supervisory relevance is required "
                    "but enabled=false."
                )

            return {
                "enabled": False,
                "required": (
                    self.required
                ),
                "status": "DISABLED",
            }

        if not self.model_path_value:

            raise RuntimeError(
                "supervisory_relevance.model_path "
                "is empty."
            )

        model_path = (
            self.model_path
        )

        if not model_path.exists():

            raise FileNotFoundError(
                "Fine-tuned supervisory model "
                "directory does not exist: "
                f"{model_path}"
            )

        if not model_path.is_dir():

            raise RuntimeError(
                "Fine-tuned supervisory model_path "
                "is not a directory: "
                f"{model_path}"
            )

        config_path = (
            model_path
            / "config.json"
        )

        if not config_path.exists():

            raise FileNotFoundError(
                "Fine-tuned supervisory model "
                "config.json is missing: "
                f"{config_path}"
            )

        if not self._model_weight_exists(
            model_path
        ):

            raise FileNotFoundError(
                "Fine-tuned supervisory model "
                "weights were not found under: "
                f"{model_path}"
            )

        if load_model:

            self._ensure_loaded()

        return (
            self.metadata()
        )


    def _ensure_loaded(
        self,
    ) -> None:

        if self._model is not None:

            return

        if not self.enabled:

            return

        try:

            import torch

            from transformers import (
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )

        except ImportError as exc:

            raise RuntimeError(
                "Supervisory relevance dependencies "
                "are not installed. "
                "Run the same ML requirements used "
                "for FinBERT."
            ) from exc

        if (
            self.device_setting
            == "auto"
        ):

            if torch.cuda.is_available():

                device = (
                    torch.device(
                        "cuda"
                    )
                )

            elif (
                getattr(
                    torch.backends,
                    "mps",
                    None,
                )
                and torch.backends.mps.is_available()
            ):

                device = (
                    torch.device(
                        "mps"
                    )
                )

            else:

                device = (
                    torch.device(
                        "cpu"
                    )
                )

        else:

            device = (
                torch.device(
                    self.device_setting
                )
            )

        # The frozen trainer/evaluator loads the tokenizer from the
        # fine-tuned checkpoint. Prefer that exact local artifact when
        # tokenizer files are present. Fall back only to the configured
        # tokenizer name, still in local/offline mode.
        checkpoint_tokenizer_files = (
            "tokenizer_config.json",
            "tokenizer.json",
            "vocab.txt",
            "special_tokens_map.json",
        )

        if any(
            (
                self.model_path
                / name
            ).exists()

            for name
            in checkpoint_tokenizer_files
        ):

            tokenizer_source = str(
                self.model_path
            )

        else:

            tokenizer_source = (
                self.tokenizer_name
                or str(
                    self.model_path
                )
            )

        tokenizer = (
            AutoTokenizer
            .from_pretrained(
                tokenizer_source,
                local_files_only=True,
            )
        )

        model = (
            AutoModelForSequenceClassification
            .from_pretrained(
                str(
                    self.model_path
                ),
                local_files_only=True,
            )
        )

        model.to(
            device
        )

        model.eval()

        num_labels = int(
            model.config.num_labels
        )

        if (
            num_labels
            != len(
                self.label_order
            )
        ):

            raise RuntimeError(
                "Fine-tuned supervisory model "
                f"num_labels={num_labels}, but "
                "ml.yaml label_order contains "
                f"{len(self.label_order)} labels."
            )

        configured_label_by_index = {
            index: label

            for index, label
            in enumerate(
                self.label_order
            )
        }

        model_label_by_index = {}

        for (
            raw_index,
            raw_label,
        ) in (
            getattr(
                model.config,
                "id2label",
                {},
            )
            or {}
        ).items():

            try:

                index = int(
                    raw_index
                )

            except (
                TypeError,
                ValueError,
            ):

                continue

            label = str(
                raw_label
            ).strip().upper()

            if (
                label
                in self.label_order
            ):

                model_label_by_index[
                    index
                ] = (
                    label
                )

        # Prefer explicit semantic labels saved inside the model
        # only when every class is present. Otherwise use the
        # training label order recorded in ml.yaml.
        if (
            len(
                model_label_by_index
            )
            == num_labels
            and set(
                model_label_by_index
                .values()
            )
            == set(
                self.label_order
            )
        ):

            label_by_index = (
                model_label_by_index
            )

        else:

            label_by_index = (
                configured_label_by_index
            )

        self._torch = torch

        self._tokenizer = tokenizer

        self._model = model

        self._device = device

        self._label_by_index = (
            label_by_index
        )


    @staticmethod
    def spaced_indices(
        total: int,
        maximum: int,
    ) -> list[int]:
        """
        Match the frozen trainer exactly:

        retain windows from the beginning, middle and end
        rather than taking only the first N windows.
        """

        if maximum < 1:

            raise ValueError(
                "maximum chunks must be >= 1"
            )

        if total <= maximum:

            return list(
                range(
                    total
                )
            )

        if maximum == 1:

            return [
                0
            ]

        return sorted(
            {
                round(
                    index
                    * (
                        total
                        - 1
                    )
                    / (
                        maximum
                        - 1
                    )
                )

                for index
                in range(
                    maximum
                )
            }
        )


    def _encode_windows(
        self,
        text: str,
        *,
        maximum: int,
    ) -> list[dict]:
        """
        Tokenize exactly like the frozen trainer.

        No padding is introduced here. Each selected window is
        later sent to the model separately, matching training /
        frozen evaluation inference semantics.
        """

        text = str(
            text
            or ""
        ).strip()

        if not text:

            return []

        payload = self._tokenizer(
            text,
            max_length=(
                self.max_length
            ),
            truncation=True,
            stride=(
                self.stride
            ),
            return_overflowing_tokens=True,
            padding=False,
        )

        payload.pop(
            "overflow_to_sample_mapping",
            None,
        )

        count = len(
            payload[
                "input_ids"
            ]
        )

        windows = []

        for index in self.spaced_indices(
            count,
            maximum,
        ):

            item = {
                key: value[
                    index
                ]

                for key, value
                in payload.items()
            }

            if (
                self.chunk_weighting
                == "token_count"
            ):

                item[
                    "token_weight"
                ] = float(
                    sum(
                        item[
                            "attention_mask"
                        ]
                    )
                )

            else:

                item[
                    "token_weight"
                ] = 1.0

            windows.append(
                item
            )

        return windows


    def _predict_text(
        self,
        text: str,
        *,
        max_chunks: (
            int
            | None
        ) = None,
    ) -> tuple[
        Any
        | None,
        int,
    ]:
        """
        Return token-weighted RAW LOGITS for one article side.

        Critical parity rule:
            do not softmax individual chunks.

        The frozen trainer token-weights raw chunk logits, then
        combines headline/body logits, and applies softmax only
        once at final article level.
        """

        self._ensure_loaded()

        text = str(
            text
            or ""
        ).strip()

        if not text:

            return (
                None,
                0,
            )

        maximum = (
            int(
                max_chunks
            )
            if max_chunks is not None
            else 2_147_483_647
        )

        windows = self._encode_windows(
            text,
            maximum=(
                maximum
            ),
        )

        if not windows:

            return (
                None,
                0,
            )

        model = (
            self._model
        )

        torch = (
            self._torch
        )

        logits = []

        weights = []

        with torch.inference_mode():

            for window in windows:

                features = {
                    key: torch.tensor(
                        [
                            value
                        ],
                        dtype=(
                            torch.long
                        ),
                        device=(
                            self._device
                        ),
                    )

                    for key, value
                    in window.items()

                    if key
                    != "token_weight"
                }

                output = model(
                    **features,
                    return_dict=True,
                )

                logits.append(
                    output.logits.squeeze(
                        0
                    )
                )

                weights.append(
                    window[
                        "token_weight"
                    ]
                )

        token_weights = torch.tensor(
            weights,
            device=(
                self._device
            ),
            dtype=(
                logits[
                    0
                ].dtype
            ),
        )

        weight_sum = (
            token_weights.sum()
        )

        if float(
            weight_sum
            .detach()
            .cpu()
            .item()
        ) <= 0:

            raise RuntimeError(
                "Fine-tuned supervisory relevance "
                "model produced zero chunk weight."
            )

        normalized = (
            token_weights
            / weight_sum
        )

        side_logits = torch.stack(
            logits,
            dim=0,
        )

        pooled_logits = (
            side_logits
            * normalized[
                :,
                None,
            ]
        ).sum(
            dim=0
        )

        return (
            pooled_logits,
            len(
                windows
            ),
        )


    def analyse(
        self,
        *,
        headline: str,
        article_text: str,
    ) -> SupervisoryRelevanceAnalysis:

        if not self.enabled:

            return (
                SupervisoryRelevanceAnalysis(
                    enabled=False,
                    status="DISABLED",
                    model_id=(
                        self.model_id
                    ),
                    model_path=(
                        str(
                            self.model_path
                        )
                        if self.model_path_value
                        else ""
                    ),
                    predicted_label=None,
                    confidence=None,
                    probabilities={},
                    headline_chunks=0,
                    body_chunks=0,
                    max_body_chunks=(
                        self.max_body_chunks
                    ),
                    device=None,
                    provenance_status=(
                        self.provenance_status
                    ),
                    production_role=(
                        self.production_role
                    ),
                )
            )

        headline = str(
            headline
            or ""
        ).strip()

        article_text = str(
            article_text
            or ""
        ).strip()

        (
            headline_logits,
            headline_chunks,
        ) = (
            self._predict_text(
                headline,
                max_chunks=1,
            )
        )

        (
            body_logits,
            body_chunks,
        ) = (
            self._predict_text(
                article_text,
                max_chunks=(
                    self.max_body_chunks
                ),
            )
        )

        sides = []

        if (
            headline_logits
            is not None
        ):

            sides.append(
                (
                    headline_logits,
                    self.headline_weight,
                )
            )

        if (
            body_logits
            is not None
        ):

            sides.append(
                (
                    body_logits,
                    self.body_weight,
                )
            )

        if not sides:

            raise ValueError(
                "Document has no usable text."
            )

        denominator = sum(
            weight

            for _,
            weight
            in sides
        )

        if denominator <= 0:

            raise RuntimeError(
                "Fine-tuned supervisory relevance "
                "headline/body weight denominator "
                "is not positive."
            )

        # Match model_article_logits() in the frozen trainer:
        # combine token-weighted side logits first.
        combined_logits = sum(
            value
            * (
                weight
                / denominator
            )

            for value,
            weight
            in sides
        )

        # Apply softmax ONCE after final article-level raw-logit
        # aggregation. Never average per-chunk probabilities.
        probabilities_by_index = (
            self._torch
            .softmax(
                combined_logits,
                dim=-1,
            )
            .detach()
            .cpu()
            .tolist()
        )

        combined = {
            label: 0.0

            for label
            in self.label_order
        }

        for (
            class_index,
            probability,
        ) in enumerate(
            probabilities_by_index
        ):

            label = (
                self._label_by_index[
                    class_index
                ]
            )

            combined[
                label
            ] = float(
                probability
            )

        predicted_label = max(
            combined,
            key=(
                combined.get
            ),
        )

        confidence = float(
            combined[
                predicted_label
            ]
        )

        return (
            SupervisoryRelevanceAnalysis(
                enabled=True,
                status="SUCCESS",
                model_id=(
                    self.model_id
                ),
                model_path=(
                    str(
                        self.model_path
                    )
                ),
                predicted_label=(
                    predicted_label
                ),
                confidence=(
                    confidence
                ),
                probabilities={
                    label: float(
                        combined[
                            label
                        ]
                    )

                    for label
                    in self.label_order
                },
                headline_chunks=(
                    headline_chunks
                ),
                body_chunks=(
                    body_chunks
                ),
                max_body_chunks=(
                    self.max_body_chunks
                ),
                device=(
                    str(
                        self._device
                    )
                ),
                provenance_status=(
                    self.provenance_status
                ),
                production_role=(
                    self.production_role
                ),
            )
        )


    def metadata(
        self,
    ) -> dict:

        return {
            "enabled": (
                self.enabled
            ),
            "required": (
                self.required
            ),
            "model_id": (
                self.model_id
            ),
            "model_path": (
                str(
                    self.model_path
                )
                if self.model_path_value
                else ""
            ),
            "tokenizer_name": (
                self.tokenizer_name
            ),
            "label_order": (
                self.label_order
            ),
            "max_length": (
                self.max_length
            ),
            "stride": (
                self.stride
            ),
            "batch_size": (
                self.batch_size
            ),
            "headline_weight": (
                self.headline_weight
            ),
            "body_weight": (
                self.body_weight
            ),
            "max_body_chunks": (
                self.max_body_chunks
            ),
            "chunk_weighting": (
                self.chunk_weighting
            ),
            "device": (
                str(
                    self._device
                )
                if self._device is not None
                else None
            ),
            "experiment_name": (
                self.experiment_name
            ),
            "expected_best_epoch": (
                self.expected_best_epoch
            ),
            "expected_validation_macro_f1": (
                self.expected_validation_macro_f1
            ),
            "provenance_status": (
                self.provenance_status
            ),
            "production_role": (
                self.production_role
            ),
        }
