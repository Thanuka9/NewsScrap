"""Stream child output to the terminal and retain a UTF-8 per-stage log."""
from __future__ import annotations

from collections import deque
import os
from pathlib import Path
import subprocess
import sys
import threading

from .reliability import run_process


def run_logged_process(command, *, log_path: Path, cwd=None, timeout=3600,
                       runner=run_process):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    tail = deque(maxlen=30)
    env = os.environ.copy()
    env.update(PYTHONUNBUFFERED='1', PYTHONIOENCODING='utf-8')

    def follow():
        with log_path.open(encoding='utf-8', errors='replace') as reader:
            while True:
                line = reader.readline()
                if line:
                    tail.append(line.rstrip())
                    try:
                        print(line, end='', flush=True)
                    except UnicodeEncodeError:
                        print(line.encode(sys.stdout.encoding or 'utf-8', errors='backslashreplace')
                              .decode(sys.stdout.encoding or 'utf-8'), end='', flush=True)
                elif stop.is_set():
                    break
                else:
                    stop.wait(0.1)

    with log_path.open('w', encoding='utf-8') as output:
        follower = threading.Thread(target=follow, daemon=True)
        follower.start()
        try:
            result = runner(command, cwd=cwd, check=False, timeout=timeout,
                            stdout=output, stderr=subprocess.STDOUT, env=env)
        finally:
            output.flush()
            stop.set()
            follower.join(timeout=10)
    result.log_path = str(log_path.resolve())
    result.output_tail = list(tail)
    return result
