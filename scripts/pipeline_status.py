"""Read-only release, process, log and recovery status for a publication date."""
from __future__ import annotations
import argparse
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from bank_intel.common.readiness import final_stage_readiness
from bank_intel.publication.contract import validate_release


def read(path):
    try:
        result = json.loads(path.read_text(encoding='utf-8-sig'))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None if ctypes.get_last_error() == 5 else False
        try:
            code = wintypes.DWORD()
            return code.value == 259 if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) else None
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None


def status(root, target):
    root = Path(root).resolve()
    try:
        release = validate_release(root, target)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        release = {'status': 'NOT_RELEASED_OR_INVALID', 'detail': str(exc)}
    owner = read(root/'data/runtime/pipeline_owner.json')
    alive = process_alive(owner.get('pid'))
    stage = read(root/'data/runtime/current_step.json')
    stage_alive = process_alive(stage.get('pid'))
    running = bool(owner) and alive is True
    if stage:
        stage['process_alive'] = stage_alive
    # A killed process can leave RUNNING metadata; never present it as active.
    if stage.get('status') == 'RUNNING' and (stage_alive is False or not running):
        stage['status'] = 'INTERRUPTED_OR_STALE'
    summaries = []
    for path in (root/'data/run_summaries').glob('*.json'):
        data = read(path)
        if (data.get('target_publication_date') or data.get('target_date') or data.get('date') or data.get('publication_date')) == target:
            summaries.append((path, data))
    latest = max(summaries, key=lambda item: item[0].stat().st_mtime, default=None)
    freeze = read(root/'config/production_freeze.json')
    changes = []
    for item in freeze.get('files', []):
        path = root/item['path']
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest().lower() != item['sha256'].lower():
            changes.append(item['path'])
    return {'date': target, 'release': release, 'pipeline_running': running,
            'owner_pid': owner.get('pid'), 'owner_process_alive': alive,
            'last_stage': stage, 'latest_run_summary': str(latest[0]) if latest else None,
            'latest_run_status': (latest[1].get('status') or latest[1].get('final_status')) if latest else None,
            'resume_preflight': final_stage_readiness(root, target),
            'freeze_present': bool(freeze.get('files')), 'freeze_changes': changes}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    date.fromisoformat(args.date)
    result = status(ROOT, args.date)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=True))
        return 0
    print('Edition:', result['date'], result['release']['status'])
    print('Pipeline running:', result['pipeline_running'])
    stage = result['last_stage']
    if stage:
        print('Last observed stage (may belong to another date):', stage.get('name'), stage.get('status'))
        print('Stage log:', stage.get('log_path'))
    print('Latest run:', result['latest_run_status'], result['latest_run_summary'])
    print('Final-stage inputs ready:', result['resume_preflight']['ready'])
    for issue in result['resume_preflight']['issues']:
        print(' -', issue)
    print('Production freeze:', 'MATCH' if result['freeze_present'] and not result['freeze_changes'] else 'MISSING OR CHANGED')
    if result['release']['status'] == 'ALREADY_SENT':
        print('Sent edition is immutable; no rerun is needed.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
