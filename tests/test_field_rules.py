"""Unit tests for beverage-aware required-element rules (27 CFR 4/5/7)."""

import pytest

from app.verification import _beverage_type, _field_status


@pytest.mark.parametrize(
    "class_type,expected",
    [
        ("Kentucky Straight Bourbon Whiskey", "spirits"),
        ("Tequila Añejo", "spirits"),
        ("India Pale Ale", "malt"),
        ("Lager Beer", "malt"),
        ("Cabernet Sauvignon", "wine"),
        ("Red Table Wine", "wine"),
        (None, "spirits"),  # unknown defaults to strictest rules
    ],
)
def test_beverage_classification(class_type, expected):
    assert _beverage_type(class_type) == expected


def _status(key, value=None, beverage="spirits", class_type=None, imported=False,
            confidence="high"):
    return _field_status(
        key, value is not None, value, confidence, beverage, class_type, imported
    )


def test_present_value_passes():
    status, _ = _status("alcohol_content", "45% Alc./Vol.")
    assert status == "pass"


def test_low_confidence_needs_review():
    status, _ = _status("brand_name", "OLD TOM", confidence="low")
    assert status == "warning"


def test_missing_alcohol_fails_for_spirits():
    status, note = _status("alcohol_content", beverage="spirits")
    assert status == "fail"


def test_missing_alcohol_ok_for_beer():
    status, note = _status("alcohol_content", beverage="malt")
    assert status == "pass"
    assert "7.65" in note


def test_missing_alcohol_on_table_wine_passes():
    status, note = _status(
        "alcohol_content", beverage="wine", class_type="Red Table Wine"
    )
    assert status == "pass"
    assert "4.36" in note


def test_missing_alcohol_on_other_wine_needs_review():
    status, note = _status(
        "alcohol_content", beverage="wine", class_type="Cabernet Sauvignon"
    )
    assert status == "warning"
    assert "4.36" in note


def test_missing_country_ok_for_domestic():
    status, note = _status("country_of_origin", imported=False)
    assert status == "pass"
    assert "domestic" in note


def test_missing_country_fails_for_import():
    status, note = _status("country_of_origin", imported=True)
    assert status == "fail"
    assert "import" in note.lower()


def test_missing_bottler_address_fails_with_citation():
    status, note = _status("bottler_address")
    assert status == "fail"
    assert "5.66" in note


def test_missing_brand_fails():
    status, _ = _status("brand_name")
    assert status == "fail"


def _country_analysis(country_value, bottler_value):
    return {
        "fields": {
            "country_of_origin": {"value": country_value, "present": bool(country_value), "notes": ""},
            "bottler_address": {"value": bottler_value, "present": True, "notes": ""},
        }
    }


def test_country_inferred_from_address_is_dropped():
    from app.verification import _drop_inferred_country

    analysis = _country_analysis("Japan", "Brewed and Bottled by Kiyomi Shuzo, Kyoto, Japan")
    _drop_inferred_country(analysis)
    country = analysis["fields"]["country_of_origin"]
    assert country["value"] is None and country["present"] is False
    assert "explicit" in country["notes"]


def test_explicit_country_statement_is_kept():
    from app.verification import _drop_inferred_country

    analysis = _country_analysis("Product of Mexico", "Produced by Casa Dorada, Jalisco, Mexico")
    _drop_inferred_country(analysis)
    assert analysis["fields"]["country_of_origin"]["value"] == "Product of Mexico"


def test_standalone_country_not_in_address_is_kept():
    from app.verification import _drop_inferred_country

    analysis = _country_analysis("Scotland", "Bottled by Glen Marrow Distillers, Speyside")
    _drop_inferred_country(analysis)
    assert analysis["fields"]["country_of_origin"]["value"] == "Scotland"
