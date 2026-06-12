"""Unit tests for field comparison and verdict folding."""

import pytest

from app.compare import apply_expected, compare_field
from app.schemas import FieldResult, LabelResult, WarningResult
from app.warning import GOVERNMENT_WARNING_TEXT


@pytest.mark.parametrize(
    "extracted,expected",
    [
        ("45% Alc./Vol. (90 Proof)", "45% ABV"),
        ("45% ABV", "90 Proof"),
        ("Alc. 13.5% by Vol.", "13.5%"),
    ],
)
def test_equivalent_alcohol_formats_pass(extracted, expected):
    status, note = compare_field("alcohol_content", extracted, expected)
    assert status == "pass"
    assert "different format" in note


def test_different_alcohol_fails():
    status, _ = compare_field("alcohol_content", "40% Alc./Vol.", "45% ABV")
    assert status == "fail"


@pytest.mark.parametrize(
    "extracted,expected",
    [
        ("750 mL", "750ML"),
        ("750 mL", "75 cl"),
        ("750 mL", "0.75 L"),
        ("25.4 FL OZ", "751 ml"),  # within 1 mL tolerance
    ],
)
def test_equivalent_net_contents_pass(extracted, expected):
    status, _ = compare_field("net_contents", extracted, expected)
    assert status == "pass"


def test_different_net_contents_fails():
    status, _ = compare_field("net_contents", "750 mL", "1 L")
    assert status == "fail"


def test_european_decimal_comma_volume():
    status, _ = compare_field("net_contents", "0,04 l", "0.04 L")
    assert status == "pass"


def test_thousands_separator_volume():
    status, _ = compare_field("net_contents", "1,000 mL", "1 L")
    assert status == "pass"


def test_unicode_compound_word_needs_review_not_fail():
    status, note = compare_field(
        "class_type", "Kräuter-Likör", "Herbal Liqueur (Kräuterlikör)"
    )
    assert status == "warning"
    assert "confirm" in note


def test_brand_case_difference_passes_with_note():
    status, note = compare_field("brand_name", "STONE'S THROW", "Stone's Throw")
    assert status == "pass"
    assert "formatting differs" in note


def test_trailing_period_does_not_break_containment():
    status, note = compare_field(
        "brand_name", "JOHNNIE WALKER.", "Johnnie Walker Black Label"
    )
    assert status == "warning"
    assert "confirm" in note


def test_similar_text_needs_review():
    status, note = compare_field(
        "class_type", "Straight Bourbon Whiskey", "Kentucky Straight Bourbon Whiskey"
    )
    assert status == "warning"
    assert "confirm" in note


def test_different_brand_fails():
    status, _ = compare_field("brand_name", "OLD TOM DISTILLERY", "Casamigos")
    assert status == "fail"


def _result() -> LabelResult:
    return LabelResult(
        filename="bottle.jpg",
        verdict="pass",
        fields=[
            FieldResult(
                field="brand_name", label="Brand Name", value="OLD TOM",
                present=True, confidence="high", status="pass",
            ),
            FieldResult(
                field="alcohol_content", label="Alcohol Content", value="45% ABV",
                present=True, confidence="high", status="pass",
            ),
        ],
        warning_statement=WarningResult(
            status="pass",
            extracted_text=GOVERNMENT_WARNING_TEXT,
            expected_text=GOVERNMENT_WARNING_TEXT,
        ),
    )


def test_apply_expected_mismatch_fails_label():
    result = _result()
    apply_expected(result, {"brand_name": "Casamigos"})
    brand = result.fields[0]
    assert brand.expected_value == "Casamigos"
    assert brand.status == "fail"
    assert result.verdict == "fail"


def test_apply_expected_equivalent_keeps_pass():
    result = _result()
    apply_expected(result, {"brand_name": "Old Tom", "alcohol_content": "90 Proof"})
    assert result.verdict == "pass"
    assert all(f.status == "pass" for f in result.fields)


def test_apply_expected_no_match_adds_note():
    result = _result()
    apply_expected(result, None)
    assert result.verdict == "pass"
    assert any("catalog" in note for note in result.notes)
