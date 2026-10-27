from __future__ import annotations

import argparse
from pathlib import Path

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_FILE = (
    PROJECT_ROOT
    / "config"
    / "sources"
    / "registry.yaml"
)


DEFAULTS = {
    "daily_mirror": {
        "name": "Daily Mirror",
        "enabled": False,
        "production_status": "TESTING",
        "section_url": "https://www.dailymirror.lk/business",
    },
    "island": {
        "name": "The Island",
        "enabled": False,
        "production_status": "TESTING",
        "section_url": "https://island.lk/category/business/",
    },
}


def load_registry() -> dict:
    if not REGISTRY_FILE.exists():
        raise FileNotFoundError(
            f"Registry not found: {REGISTRY_FILE}"
        )

    payload = yaml.safe_load(
        REGISTRY_FILE.read_text(encoding="utf-8")
    ) or {}

    sources = payload.setdefault("sources", {})
    if not isinstance(sources, dict):
        raise ValueError("registry.yaml sources must be a mapping")

    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--activate",
        choices=("daily_mirror", "island", "both"),
        help=(
            "Activate only after the individual collector has "
            "passed QA. Omit this flag for safe TESTING setup."
        ),
    )
    args = parser.parse_args()

    payload = load_registry()
    sources = payload["sources"]

    for key, defaults in DEFAULTS.items():
        current = sources.setdefault(key, {})
        for field, value in defaults.items():
            current.setdefault(field, value)

        # Safe default: do not activate accidentally.
        if args.activate in {key, "both"}:
            current["enabled"] = True
            current["production_status"] = "ACTIVE"
        elif not current.get("enabled"):
            current["enabled"] = False
            if str(current.get("production_status", "")).upper() != "ACTIVE":
                current["production_status"] = "TESTING"

    backup = REGISTRY_FILE.with_suffix(".yaml.before_multisource.bak")
    if not backup.exists():
        backup.write_text(
            REGISTRY_FILE.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    REGISTRY_FILE.write_text(
        yaml.safe_dump(
            payload,
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    print(f"Updated: {REGISTRY_FILE}")
    print(f"Backup : {backup}")
    for key in ("daily_mirror", "island"):
        cfg = sources[key]
        print(
            f"{key:14} enabled={cfg.get('enabled')} "
            f"status={cfg.get('production_status')}"
        )


if __name__ == "__main__":
    main()
