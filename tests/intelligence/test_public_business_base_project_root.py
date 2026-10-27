from bank_intel.collectors.sources import _public_business_base as base


def test_project_root_points_to_repository_root():
    assert (base.PROJECT_ROOT / "scripts").is_dir()
    assert (base.PROJECT_ROOT / "config").is_dir()
    assert (base.PROJECT_ROOT / "src").is_dir()


def test_staged_root_is_project_data_staged():
    assert base.STAGED_ROOT == (
        base.PROJECT_ROOT
        / "data"
        / "staged"
    )

    assert "src\\bank_intel\\data\\staged" not in str(
        base.STAGED_ROOT
    ).lower()
