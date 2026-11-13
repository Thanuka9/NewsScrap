"""Keep tests out of the operational data folders."""
import pytest


@pytest.fixture(autouse=True)
def isolated_source_status(tmp_path, monkeypatch):
    # Collector tests call the real writer; send its files to a temporary root.
    from bank_intel.common import source_status
    status_root = tmp_path / 'source_status'
    monkeypatch.setattr(source_status, 'STATUS_ROOT', status_root)
    monkeypatch.setattr(source_status, 'HISTORY_ROOT', status_root / 'history')


@pytest.fixture(autouse=True)
def rule_based_grouping(monkeypatch, request):
    # Loading FinBERT in every grouping test would be slow; the semantic pass
    # has its own test that uses the real model.
    if 'semantic_model' not in request.keywords:
        from bank_intel.publication import reconcile
        monkeypatch.setattr(reconcile, 'SEMANTIC_MATCHING', False)


@pytest.fixture(autouse=True)
def no_live_writing(monkeypatch):
    # A key on the developer's machine must never make a test call the paid API.
    monkeypatch.setenv('NEWS_WRITING', '0')


def pytest_configure(config):
    config.addinivalue_line('markers', 'semantic_model: loads the real FinBERT model for the semantic grouping pass')
