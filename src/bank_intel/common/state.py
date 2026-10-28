from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


class JsonArticleState:
    """
    Temporary local article-state store.

    This will later be replaced by SQL Server / SQLAlchemy.

    Stores:
        - URLs already discovered
        - last content hash
        - first seen time
        - last seen time
        - last fetched time
        - version number
        - publication date
        - headline
    """

    def __init__(self, path: Path):
        self.path = path

        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.data: dict[str, Any] = {
            "articles": {}
        }

        self.load()

    @staticmethod
    def now_utc() -> str:
        return datetime.now(
            timezone.utc
        ).isoformat()

    def load(self) -> None:
        """
        Load existing state file if it exists.
        """

        if not self.path.exists():
            return

        try:
            loaded = json.loads(
                self.path.read_text(
                    encoding="utf-8"
                )
            )

            if isinstance(loaded, dict):
                self.data = loaded

            if "articles" not in self.data:
                self.data["articles"] = {}

        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError(f'CORRUPT_ARTICLE_STATE: {self.path}; restore a verified snapshot before collection') from exc
        if not isinstance(loaded, dict) or not isinstance(loaded.get('articles'), dict):
            raise RuntimeError(f'INVALID_ARTICLE_STATE_SCHEMA: {self.path}')

    def save(self) -> None:
        """
        Persist state atomically.
        """

        from .reliability import atomic_json
        if self.path.is_file():
            # Preserve only a validated last-good snapshot.
            previous = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(previous, dict) or not isinstance(previous.get('articles'), dict):
                raise RuntimeError(f'INVALID_ARTICLE_STATE_SCHEMA: {self.path}')
            atomic_json(self.path.with_suffix('.last_good.json'), previous)
        atomic_json(self.path, self.data)

    def get(
        self,
        canonical_url: str,
    ) -> dict | None:

        return self.data[
            "articles"
        ].get(canonical_url)

    def is_known(
        self,
        canonical_url: str,
    ) -> bool:

        return canonical_url in self.data[
            "articles"
        ]

    def last_content_hash(
        self,
        canonical_url: str,
    ) -> str | None:

        record = self.get(
            canonical_url
        )

        if not record:
            return None

        return record.get(
            "content_hash"
        )

    def version_number(
        self,
        canonical_url: str,
    ) -> int:

        record = self.get(
            canonical_url
        )

        if not record:
            return 0

        return int(
            record.get(
                "version_number",
                0,
            )
        )

    def last_fetched_at(
        self,
        canonical_url: str,
    ) -> str | None:

        record = self.get(
            canonical_url
        )

        if not record:
            return None

        return record.get(
            "last_fetched_at"
        )

    def mark_seen(
        self,
        *,
        canonical_url: str,
        source_url: str,
        headline: str | None = None,
    ) -> None:

        now = self.now_utc()

        articles = self.data[
            "articles"
        ]

        if canonical_url not in articles:

            articles[
                canonical_url
            ] = {
                "source_url": source_url,
                "headline": headline,
                "content_hash": None,
                "published_at": None,
                "first_seen_at": now,
                "last_seen_at": now,
                "last_fetched_at": None,
                "version_number": 0,
            }

        else:

            record = articles[
                canonical_url
            ]

            record[
                "last_seen_at"
            ] = now

            if headline:
                record[
                    "headline"
                ] = headline

    def update_after_fetch(
        self,
        *,
        canonical_url: str,
        source_url: str,
        headline: str,
        content_hash: str,
        published_at: str | None,
        changed: bool,
    ) -> int:
        """
        Update article state after fetching.

        Returns the article version number.
        """

        now = self.now_utc()

        articles = self.data[
            "articles"
        ]

        record = articles.get(
            canonical_url
        )

        if record is None:

            record = {
                "source_url": source_url,
                "headline": headline,
                "content_hash": None,
                "published_at": None,
                "first_seen_at": now,
                "last_seen_at": now,
                "last_fetched_at": None,
                "version_number": 0,
            }

            articles[
                canonical_url
            ] = record

        if changed:

            record[
                "version_number"
            ] = (
                int(
                    record.get(
                        "version_number",
                        0,
                    )
                )
                + 1
            )

        record[
            "source_url"
        ] = source_url

        record[
            "headline"
        ] = headline

        record[
            "content_hash"
        ] = content_hash

        record[
            "published_at"
        ] = published_at

        record[
            "last_seen_at"
        ] = now

        record[
            "last_fetched_at"
        ] = now

        return int(
            record[
                "version_number"
            ]
        )
