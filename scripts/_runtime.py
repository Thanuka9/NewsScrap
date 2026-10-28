"""Shared runtime for standalone script entrypoints."""
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from bank_intel.common.reliability import project_lock, run_process


def locked_main(function):
    with project_lock(ROOT):
        return function()
