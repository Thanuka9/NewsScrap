"""Keep tests out of the operational data folders."""
import pytest


@pytest.fixture(autouse=True)
def isolated_source_status(tmp_path, monkeypatch):
    # Collector tests call the real writer; send its files to a temporary root.
    from bank_intel.common import source_status
    status_root = tmp_path / 'source_status'
    monkeypatch.setattr(source_status, 'STATUS_ROOT', status_root)
    monkeypatch.setattr(source_status, 'HISTORY_ROOT', status_root / 'history')
