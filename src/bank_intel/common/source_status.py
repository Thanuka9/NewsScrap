from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import time
import uuid
from zoneinfo import ZoneInfo

SRI_LANKA_TZ = ZoneInfo("Asia/Colombo")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
STATUS_ROOT = PROJECT_ROOT / "data" / "source_status"
HISTORY_ROOT = STATUS_ROOT / "history"

def get_run_id() -> str:
    value = os.environ.get("BANK_INTEL_RUN_ID")
    if value:
        return value
    now = datetime.now(SRI_LANKA_TZ)
    return now.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]

def current_status_path(source_key: str) -> Path:
    return STATUS_ROOT / f"{source_key}.json"

def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    temp_path.write_text(text, encoding="utf-8")
    for attempt in range(6):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError:
            if attempt == 5:
                break
            time.sleep(0.15 * (attempt + 1))
    path.write_text(text, encoding="utf-8")
    try:
        if temp_path.exists():
            temp_path.unlink()
    except OSError:
        pass

def write_source_status(*, source_key: str, payload: dict) -> Path:
    now = datetime.now(SRI_LANKA_TZ)
    run_id = payload.get("run_id") or get_run_id()
    complete_payload = {**payload, "source_key": source_key, "run_id": run_id, "status_written_at": now.isoformat()}
    current_path = current_status_path(source_key)
    _atomic_write_json(current_path, complete_payload)
    history_path = HISTORY_ROOT / now.date().isoformat() / f"{run_id}_{source_key}.json"
    _atomic_write_json(history_path, complete_payload)
    return current_path

def load_source_status(source_key: str) -> dict | None:
    path = current_status_path(source_key)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
