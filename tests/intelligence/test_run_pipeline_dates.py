from scripts.run_pipeline import parse_args, resolve_target_date


def test_from_date_and_days_resolve_to_inclusive_end_date(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "run_pipeline.py",
            "--from-date",
            "2026-10-12",
            "--days",
            "3",
            "--skip-collection",
        ],
    )

    args = parse_args()

    assert resolve_target_date(
        date_value=args.date,
        from_date_value=args.from_date,
        days=args.days,
        today="2026-10-14",
    ) == "2026-10-14"


def test_date_option_remains_the_reporting_window_end(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["run_pipeline.py", "--date", "2026-10-14", "--days", "3"],
    )

    args = parse_args()

    assert resolve_target_date(
        date_value=args.date,
        from_date_value=args.from_date,
        days=args.days,
        today="2026-10-14",
    ) == "2026-10-14"
