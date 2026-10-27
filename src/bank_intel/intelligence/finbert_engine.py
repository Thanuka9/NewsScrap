from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any
import math

import numpy as np
import yaml


PROJECT_ROOT = (
    Path(__file__)
    .resolve()
    .parents[3]
)

DEFAULT_ML_CONFIG = (
    PROJECT_ROOT
    / "config"
    / "intelligence"
    / "ml.yaml"
)


def load_ml_config(
    path: Path | str = DEFAULT_ML_CONFIG,
) -> dict:

    return yaml.safe_load(
        Path(path).read_text(
            encoding="utf-8-sig"
        )
    )


@dataclass(frozen=True)
class FinBertAnalysis:
    model_name: str
    sentiment_label: str
    sentiment_confidence: float
    sentiment_probabilities: dict[str, float]
    headline_chunks: int
    body_chunks: int
    embedding_dimension: int
    device: str

    def to_dict(self) -> dict:
        return asdict(self)


class FinBertEngine:
    """
    Finance-domain NLP engine built on ProsusAI/finbert.

    Uses the model in two ways:

    1. Existing financial-sentiment classification head
       (positive / negative / neutral).

    2. Final hidden-state representation to produce a
       finance-domain article embedding.

    The existing FinBERT sentiment head is NOT treated as a
    supervisory relevance classifier.
    """

    def __init__(
        self,
        *,
        config: dict | None = None,
    ) -> None:

        self.config = (
            config
            or load_ml_config()
        )

        finbert = self.config[
            "finbert"
        ]

        self.model_name = str(
            finbert[
                "model_name"
            ]
        )

        self.max_length = int(
            finbert.get(
                "max_length",
                512,
            )
        )

        self.stride = int(
            finbert.get(
                "stride",
                96,
            )
        )

        self.batch_size = int(
            finbert.get(
                "batch_size",
                8,
            )
        )

        self.headline_weight = float(
            finbert.get(
                "headline_weight",
                0.35,
            )
        )

        self.body_weight = float(
            finbert.get(
                "body_weight",
                0.65,
            )
        )

        self.device_setting = str(
            finbert.get(
                "device",
                "auto",
            )
        )

        # Historical / production inference should be able to run
        # without any Hugging Face network calls once the model is
        # already cached locally.
        self.local_files_only = bool(
            finbert.get(
                "local_files_only",
                True,
            )
        )

        # ProsusAI/finbert is distributed with PyTorch weights.
        # Setting this to False prevents Transformers from trying
        # to contact Hugging Face to create/resolve a safetensors
        # conversion during offline inference.
        self.use_safetensors = bool(
            finbert.get(
                "use_safetensors",
                False,
            )
        )

        total_weight = (
            self.headline_weight
            + self.body_weight
        )

        if total_weight <= 0:
            raise ValueError(
                "FinBERT headline/body weights "
                "must have positive total weight."
            )

        self.headline_weight /= (
            total_weight
        )

        self.body_weight /= (
            total_weight
        )

        self._torch = None
        self._tokenizer = None
        self._model = None
        self._device = None

    def _ensure_loaded(
        self,
    ) -> None:

        if self._model is not None:
            return

        try:

            import torch

            from transformers import (
                AutoModelForSequenceClassification,
                AutoTokenizer,
            )

        except ImportError as exc:

            raise RuntimeError(
                "FinBERT dependencies are not installed. "
                "Run: python -m pip install "
                "-r requirements-ml.txt"
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
                    None
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

            device = torch.device(
                self.device_setting
            )

        try:

            tokenizer = (
                AutoTokenizer
                .from_pretrained(
                    self.model_name,
                    local_files_only=(
                        self.local_files_only
                    ),
                )
            )

            model = (
                AutoModelForSequenceClassification
                .from_pretrained(
                    self.model_name,
                    local_files_only=(
                        self.local_files_only
                    ),
                    use_safetensors=(
                        self.use_safetensors
                    ),
                )
            )

        except OSError as exc:

            mode = (
                "local cache only"
                if self.local_files_only
                else "normal Hugging Face resolution"
            )

            raise RuntimeError(
                "Base FinBERT could not be loaded using "
                f"{mode}. Model={self.model_name!r}. "
                "For offline historical inference the cached "
                "ProsusAI/finbert PyTorch weights must exist. "
                "If needed, temporarily allow online access once "
                "to populate the cache, then rerun offline."
            ) from exc

        model.to(
            device
        )

        model.eval()

        self._torch = torch
        self._tokenizer = tokenizer
        self._model = model
        self._device = device

    @property
    def device(
        self,
    ) -> str:

        self._ensure_loaded()

        return str(
            self._device
        )

    @staticmethod
    def _normalise_embedding(
        vector: np.ndarray,
    ) -> np.ndarray:

        vector = np.asarray(
            vector,
            dtype=np.float32,
        )

        norm = float(
            np.linalg.norm(
                vector
            )
        )

        if norm <= 0:
            return vector

        return (
            vector
            / norm
        ).astype(
            np.float32,
            copy=False,
        )

    def _analyse_text(
        self,
        text: str,
    ) -> tuple[
        dict[str, float],
        np.ndarray,
        int,
    ]:

        self._ensure_loaded()

        torch = self._torch
        tokenizer = self._tokenizer
        model = self._model

        text = str(
            text
            or ""
        ).strip()

        if not text:

            hidden_size = int(
                model.config.hidden_size
            )

            return (
                {
                    "positive": 0.0,
                    "negative": 0.0,
                    "neutral": 1.0,
                },
                np.zeros(
                    hidden_size,
                    dtype=np.float32,
                ),
                0,
            )

        encoded = tokenizer(
            text,
            max_length=(
                self.max_length
            ),
            truncation=True,
            stride=(
                self.stride
            ),
            return_overflowing_tokens=True,
            return_tensors="pt",
            padding=True,
        )

        encoded.pop(
            "overflow_to_sample_mapping",
            None,
        )

        total_chunks = int(
            encoded[
                "input_ids"
            ].shape[0]
        )

        label_map = {
            int(key): str(
                value
            ).lower()
            for key, value
            in (
                model.config.id2label
                .items()
            )
        }

        probability_accumulator = {
            "positive": 0.0,
            "negative": 0.0,
            "neutral": 0.0,
        }

        embedding_sum = None
        total_weight = 0.0

        for start in range(
            0,
            total_chunks,
            self.batch_size,
        ):

            stop = min(
                start
                + self.batch_size,
                total_chunks,
            )

            batch = {
                key: value[
                    start:stop
                ].to(
                    self._device
                )
                for key, value
                in encoded.items()
            }

            with torch.no_grad():

                output = model(
                    **batch,
                    output_hidden_states=True,
                    return_dict=True,
                )

            probabilities = (
                torch.softmax(
                    output.logits,
                    dim=-1,
                )
                .detach()
                .cpu()
                .numpy()
            )

            hidden = (
                output
                .hidden_states[-1]
            )

            mask = (
                batch[
                    "attention_mask"
                ]
                .unsqueeze(-1)
                .to(
                    hidden.dtype
                )
            )

            token_counts = (
                mask
                .sum(
                    dim=1
                )
                .clamp(
                    min=1
                )
            )

            pooled = (
                (
                    hidden
                    * mask
                )
                .sum(
                    dim=1
                )
                / token_counts
            )

            pooled = (
                pooled
                .detach()
                .cpu()
                .numpy()
            )

            chunk_weights = (
                batch[
                    "attention_mask"
                ]
                .sum(
                    dim=1
                )
                .detach()
                .cpu()
                .numpy()
                .astype(
                    np.float64
                )
            )

            for (
                row_index,
                chunk_weight,
            ) in enumerate(
                chunk_weights
            ):

                weight = float(
                    chunk_weight
                )

                for class_index in range(
                    probabilities.shape[1]
                ):

                    label = (
                        label_map.get(
                            class_index,
                            str(
                                class_index
                            ),
                        )
                    )

                    probability_accumulator[
                        label
                    ] = (
                        probability_accumulator
                        .get(
                            label,
                            0.0,
                        )
                        + (
                            float(
                                probabilities[
                                    row_index,
                                    class_index,
                                ]
                            )
                            * weight
                        )
                    )

                vector = (
                    pooled[
                        row_index
                    ]
                    .astype(
                        np.float64,
                        copy=False,
                    )
                )

                if embedding_sum is None:

                    embedding_sum = (
                        np.zeros_like(
                            vector,
                            dtype=np.float64,
                        )
                    )

                embedding_sum += (
                    vector
                    * weight
                )

                total_weight += (
                    weight
                )

        if total_weight <= 0:

            raise RuntimeError(
                "FinBERT produced zero "
                "aggregate chunk weight."
            )

        probabilities = {
            key: (
                float(value)
                / total_weight
            )
            for key, value
            in (
                probability_accumulator
                .items()
            )
        }

        probability_total = sum(
            probabilities.values()
        )

        if probability_total > 0:

            probabilities = {
                key: (
                    value
                    / probability_total
                )
                for key, value
                in probabilities.items()
            }

        embedding = (
            embedding_sum
            / total_weight
        )

        embedding = (
            self._normalise_embedding(
                embedding
            )
        )

        return (
            probabilities,
            embedding,
            total_chunks,
        )

    def analyse(
        self,
        *,
        headline: str,
        article_text: str,
    ) -> tuple[
        FinBertAnalysis,
        np.ndarray,
    ]:

        headline = str(
            headline
            or ""
        ).strip()

        article_text = str(
            article_text
            or ""
        ).strip()

        (
            headline_probs,
            headline_embedding,
            headline_chunks,
        ) = self._analyse_text(
            headline
        )

        (
            body_probs,
            body_embedding,
            body_chunks,
        ) = self._analyse_text(
            article_text
        )

        if not article_text:

            effective_headline_weight = 1.0
            effective_body_weight = 0.0

        elif not headline:

            effective_headline_weight = 0.0
            effective_body_weight = 1.0

        else:

            effective_headline_weight = (
                self.headline_weight
            )

            effective_body_weight = (
                self.body_weight
            )

        labels = set(
            headline_probs
        ) | set(
            body_probs
        )

        combined_probs = {}

        for label in labels:

            combined_probs[
                label
            ] = (
                effective_headline_weight
                * headline_probs.get(
                    label,
                    0.0,
                )
                + effective_body_weight
                * body_probs.get(
                    label,
                    0.0,
                )
            )

        total = sum(
            combined_probs.values()
        )

        if total > 0:

            combined_probs = {
                key: (
                    value
                    / total
                )
                for key, value
                in combined_probs.items()
            }

        combined_embedding = (
            effective_headline_weight
            * headline_embedding
            + effective_body_weight
            * body_embedding
        )

        combined_embedding = (
            self._normalise_embedding(
                combined_embedding
            )
        )

        sentiment_label = max(
            combined_probs,
            key=combined_probs.get,
        )

        sentiment_confidence = float(
            combined_probs[
                sentiment_label
            ]
        )

        analysis = FinBertAnalysis(
            model_name=(
                self.model_name
            ),

            sentiment_label=(
                sentiment_label
            ),

            sentiment_confidence=(
                sentiment_confidence
            ),

            sentiment_probabilities={
                key: float(
                    value
                )
                for key, value
                in sorted(
                    combined_probs.items()
                )
            },

            headline_chunks=(
                headline_chunks
            ),

            body_chunks=(
                body_chunks
            ),

            embedding_dimension=int(
                combined_embedding.shape[0]
            ),

            device=str(
                self._device
            ),
        )

        return (
            analysis,
            combined_embedding,
        )
