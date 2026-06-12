"""Unit tests for the deterministic government warning check."""

import pytest

from app.warning import GOVERNMENT_WARNING_TEXT, check_government_warning


def test_exact_text_passes():
    result = check_government_warning(GOVERNMENT_WARNING_TEXT, "yes")
    assert result.status == "pass"
    assert result.notes == []


def test_line_breaks_and_extra_spaces_are_tolerated():
    wrapped = GOVERNMENT_WARNING_TEXT.replace("Surgeon General,", "Surgeon\nGeneral, ")
    result = check_government_warning(wrapped, "yes")
    assert result.status == "pass"


def test_markdown_bold_marks_are_tolerated():
    # Vision models sometimes render bold type as markdown asterisks.
    text = GOVERNMENT_WARNING_TEXT.replace(
        "GOVERNMENT WARNING:", "**GOVERNMENT WARNING:**"
    )
    result = check_government_warning(text, "yes")
    assert result.status == "pass"


def test_curly_apostrophes_are_tolerated():
    # OCR/transcription often produces typographic quotes; wording is the same.
    text = GOVERNMENT_WARNING_TEXT.replace("women", "women")  # no-op guard
    result = check_government_warning(text.replace("'", "’"), "yes")
    assert result.status == "pass"


def test_title_case_prefix_fails():
    text = GOVERNMENT_WARNING_TEXT.replace(
        "GOVERNMENT WARNING:", "Government Warning:"
    )
    result = check_government_warning(text, "yes")
    assert result.status == "fail"
    assert any("CAPITAL" in note for note in result.notes)


def test_altered_wording_fails():
    text = GOVERNMENT_WARNING_TEXT.replace("birth defects", "health issues")
    result = check_government_warning(text, "yes")
    assert result.status == "fail"
    assert any("verbatim" in note for note in result.notes)


def test_missing_statement_fails():
    result = check_government_warning(None, "unclear")
    assert result.status == "fail"
    assert result.extracted_text is None


def test_truncated_statement_fails():
    truncated = GOVERNMENT_WARNING_TEXT[:120]
    result = check_government_warning(truncated, "yes")
    assert result.status == "fail"


def test_not_bold_downgrades_to_warning():
    result = check_government_warning(GOVERNMENT_WARNING_TEXT, "no")
    assert result.status == "warning"
    assert any("bold" in note.lower() for note in result.notes)


def test_unclear_bold_passes_with_note():
    result = check_government_warning(GOVERNMENT_WARNING_TEXT, "unclear")
    assert result.status == "pass"
    assert any("best-effort" in note for note in result.notes)


@pytest.mark.parametrize("bold", ["yes", "no", "unclear"])
def test_wording_failure_beats_bold_status(bold):
    text = GOVERNMENT_WARNING_TEXT.replace("(2)", "(2!)")
    assert check_government_warning(text, bold).status == "fail"
