from bank_intel.intelligence.entity_registry import (
    EntityRecord,
    EntityRegistry,
)

from bank_intel.intelligence.entity_roles import (
    analyse_entity_roles,
)


def build_registry() -> EntityRegistry:

    return (
        EntityRegistry(

            [
                EntityRecord(

                    canonical_name=(
                        "Sanasa Development Bank PLC"
                    ),

                    institution_type=(
                        "LICENSED_SPECIALISED_BANK"
                    ),

                    aliases=(
                        "SDB Bank",
                        "SDB",
                    ),

                    registry_section=(
                        "institutions"
                    ),
                ),


                EntityRecord(

                    canonical_name=(
                        "Pan Asia Banking Corporation PLC"
                    ),

                    institution_type=(
                        "LICENSED_COMMERCIAL_BANK"
                    ),

                    aliases=(
                        "Pan Asia Bank",
                    ),

                    registry_section=(
                        "institutions"
                    ),
                ),


                EntityRecord(

                    canonical_name=(
                        "Central Bank of Sri Lanka"
                    ),

                    institution_type=(
                        "REGULATOR"
                    ),

                    aliases=(
                        "CBSL",
                        "Central Bank",
                    ),

                    registry_section=(
                        "additional_entities"
                    ),
                ),
            ],

            version="test",

            as_of=None,
        )
    )


def test_headline_bank_becomes_primary_entity():

    result = (
        analyse_entity_roles(

            registry=(
                build_registry()
            ),

            headline=(
                "SDB Bank reports "
                "higher impairment charges"
            ),

            article_text=(
                "SDB Bank said impairment "
                "charges rose. "
                "The Central Bank monitored "
                "sector conditions."
            ),
        )
    )


    assert (
        "Sanasa Development Bank PLC"
        in result.primary_entities
    )


    sdb = next(

        item

        for item in (
            result.entities
        )

        if (
            item.canonical_name
            == "Sanasa Development Bank PLC"
        )
    )


    assert (
        "PRIMARY_ENTITY"
        in sdb.roles
    )


    assert (
        "HEADLINE_ENTITY"
        in sdb.roles
    )


    assert (
        "LICENSED_BANK_MENTION"
        in sdb.roles
    )


def test_body_only_bank_is_not_primary():

    result = (
        analyse_entity_roles(

            registry=(
                build_registry()
            ),

            headline=(
                "Dearo Venture Capital "
                "strengthens leadership"
            ),

            article_text=(
                "Dearo announced a "
                "management appointment. "
                + (
                    "Background information. "
                    * 80
                )
                + (
                    "Pan Asia Bank was mentioned "
                    "in the director's "
                    "career history."
                )
            ),

            lead_characters=300,
        )
    )


    assert (
        "Pan Asia Banking Corporation PLC"
        not in result.primary_entities
    )


    pan_asia = next(

        item

        for item in (
            result.entities
        )

        if (
            item.canonical_name
            == "Pan Asia Banking Corporation PLC"
        )
    )


    assert (
        "BODY_MENTION"
        in pan_asia.roles
    )


    assert (
        "PRIMARY_ENTITY"
        not in pan_asia.roles
    )


def test_regulator_role_is_preserved():

    result = (
        analyse_entity_roles(

            registry=(
                build_registry()
            ),

            headline=(
                "Central Bank tightens "
                "liquidity requirement"
            ),

            article_text=(
                "The Central Bank issued "
                "the new direction today."
            ),
        )
    )


    cbsl = next(

        item

        for item in (
            result.entities
        )

        if (
            item.canonical_name
            == "Central Bank of Sri Lanka"
        )
    )


    assert (
        "REGULATOR_MENTION"
        in cbsl.roles
    )


    assert (
        "PRIMARY_ENTITY"
        in cbsl.roles
    )