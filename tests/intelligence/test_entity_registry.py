from bank_intel.intelligence.entity_registry import (
    EntityRegistry,
)


# ============================================================
# SDB
# ============================================================


def test_sdb_is_recognised():

    registry = (
        EntityRegistry
        .from_yaml()
    )

    matches = (
        registry.match(
            "SDB Bank reported "
            "higher impairment "
            "charges this quarter."
        )
    )

    names = {
        item.canonical_name

        for item in (
            matches
        )
    }

    assert (
        "Sanasa Development Bank PLC"
        in names
    )


# ============================================================
# NOT A WHITELIST
# ============================================================


def test_cbsl_registry_is_not_a_whitelist():

    registry = (
        EntityRegistry
        .from_yaml()
    )

    matches = (
        registry.match(
            "Example New Finance "
            "Institution announced "
            "a liquidity event."
        )
    )

    # No registry match is completely valid.
    #
    # It does NOT mean the article/entity is rejected.

    assert (
        matches == []
    )


# ============================================================
# HNB + CBSL
# ============================================================


def test_hnb_and_cbsl_are_recognised():

    registry = (
        EntityRegistry
        .from_yaml()
    )

    matches = (
        registry.match(
            "CBSL issued a direction "
            "affecting Hatton National Bank."
        )
    )

    names = {
        item.canonical_name

        for item in (
            matches
        )
    }


    assert (
        "Central Bank of Sri Lanka"
        in names
    )


    assert (
        "Hatton National Bank PLC"
        in names
    )