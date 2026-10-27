from pathlib import Path
import importlib.util


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    return module


def test_master_collection_knows_new_collectors():
    module = load_module(
        PROJECT_ROOT / "scripts" / "run_collection.py",
        "run_collection_test_module",
    )

    assert "daily_mirror" in module.SOURCE_SCRIPTS
    assert "island" in module.SOURCE_SCRIPTS

    assert (
        module.SOURCE_SCRIPTS["daily_mirror"].name
        == "run_daily_mirror.py"
    )
    assert (
        module.SOURCE_SCRIPTS["island"].name
        == "run_island.py"
    )


def test_stage1_uses_registry_instead_of_hardcoded_source_list():
    text = (
        PROJECT_ROOT
        / "scripts"
        / "run_intelligence_stage1.py"
    ).read_text(encoding="utf-8")

    # Current implementation uses load_active_source_folders().
    assert "def load_active_source_folders()" in text

    # It must load the central source registry.
    assert "registry.yaml" in text

    # The old literal hardcoded two-source list must be gone.
    old_block = (
        'ACTIVE_SOURCE_FOLDERS = [\n'
        '    "daily_ft",\n'
        '    "ceylon_today",\n'
        ']'
    )
    assert old_block not in text

    # ACTIVE_SOURCE_FOLDERS must be derived from the registry loader.
    assert "load_active_source_folders()" in text
