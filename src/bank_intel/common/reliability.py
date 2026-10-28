"""Atomic persistence, shared entrypoint locking and bounded child processes."""
from __future__ import annotations
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import uuid


def atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def atomic_json(path: Path, payload) -> None:
    atomic_write(path, json.dumps(payload, indent=2, ensure_ascii=False))


@contextmanager
def project_lock(root: Path):
    """An OS lock released on crash; child stages share their parent's token."""
    folder = Path(root).resolve() / 'data' / 'runtime'
    folder.mkdir(parents=True, exist_ok=True)
    token_path = folder / 'pipeline_owner.json'
    inherited = os.environ.get('NEWS_PIPELINE_LOCK_TOKEN')
    if inherited:
        try:
            owner = json.loads(token_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            owner = {}
        if owner.get('token') == inherited:
            yield
            return
    stream = (folder / 'pipeline.lock').open('a+b')
    locked = False
    previous = inherited
    try:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0'); stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError as exc:
            raise RuntimeError('PIPELINE_BUSY: another collection/release process owns this project') from exc
        token = uuid.uuid4().hex
        atomic_json(token_path, {'pid': os.getpid(), 'token': token})
        os.environ['NEWS_PIPELINE_LOCK_TOKEN'] = token
        yield
    finally:
        if locked:
            token_path.unlink(missing_ok=True)
            if previous is None:
                os.environ.pop('NEWS_PIPELINE_LOCK_TOKEN', None)
            else:
                os.environ['NEWS_PIPELINE_LOCK_TOKEN'] = previous
            stream.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        stream.close()


def run_process(command, *, cwd=None, check=False, timeout=3600, **kwargs):
    """subprocess.run-compatible subset; terminate descendants on timeout."""
    if os.name == 'nt':
        kwargs['creationflags'] = kwargs.get('creationflags', 0) | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    process = subprocess.Popen(command, cwd=cwd, **kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        if os.name == 'nt':
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=30)
        else:
            os.killpg(process.pid, signal.SIGKILL)
        process.kill()
        process.communicate()
        raise
    result = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result
