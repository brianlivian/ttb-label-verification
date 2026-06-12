"""Government health warning statement check.

The warning verdict is computed deterministically here rather than delegated
to the model: 27 CFR Part 16 requires the statement verbatim, with the
"GOVERNMENT WARNING:" prefix in capital letters. The vision model is only
asked to transcribe what is printed (and judge bold styling, which is
inherently best-effort from a photo); the exact comparison happens in code so
the strictest requirement in the system never depends on model judgment.
"""

import re
from typing import Optional

from app.schemas import WarningResult

WARNING_PREFIX = "GOVERNMENT WARNING:"

# Mandatory text from 27 CFR 16.21.
GOVERNMENT_WARNING_TEXT = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should "
    "not drink alcoholic beverages during pregnancy because of the risk of "
    "birth defects. (2) Consumption of alcoholic beverages impairs your "
    "ability to drive a car or operate machinery, and may cause health "
    "problems."
)


def _normalize(text: str) -> str:
    """Collapse whitespace and unify quote characters.

    Line breaks and curly apostrophes are transcription artifacts of reading a
    printed label, not wording differences, so they should not fail the check.
    Case is deliberately preserved.
    """
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    # Vision models sometimes represent bold type as markdown
    # ("**GOVERNMENT WARNING:**") — formatting marks aren't wording.
    text = text.replace("*", "").replace("_", "")
    return re.sub(r"\s+", " ", text).strip()


def check_government_warning(
    extracted_text: Optional[str],
    prefix_appears_bold: str = "unclear",
) -> WarningResult:
    """Compare a verbatim transcription against the mandatory warning text."""
    expected = GOVERNMENT_WARNING_TEXT

    if not extracted_text or not extracted_text.strip():
        return WarningResult(
            status="fail",
            extracted_text=None,
            expected_text=expected,
            notes=["No government warning statement was found on the label."],
        )

    found = _normalize(extracted_text)
    notes: list[str] = []
    status = "pass"

    # Wording must match verbatim. Compared case-insensitively here because
    # the prefix capitalization is enforced separately below and body case is
    # not what the verbatim requirement is about.
    if found.lower() != expected.lower():
        status = "fail"
        notes.append(
            "Warning text does not match the required statement verbatim "
            "(27 CFR 16.21). Every word must appear exactly as prescribed."
        )

    # "GOVERNMENT WARNING:" must be in capital letters. Title case or mixed
    # case is a violation even when the wording is otherwise correct.
    if not found.startswith(WARNING_PREFIX):
        status = "fail"
        if found.lower().startswith(WARNING_PREFIX.lower()):
            notes.append(
                f'The label shows "{extracted_text.strip()[:len(WARNING_PREFIX)]}" — '
                'the "GOVERNMENT WARNING:" prefix must be in ALL CAPITAL letters.'
            )
        else:
            notes.append(
                'The statement must begin with "GOVERNMENT WARNING:" in '
                "capital letters."
            )

    # Bold styling is required by regulation but can only be judged
    # best-effort from an image, so it downgrades to a warning, never a fail.
    if status == "pass":
        if prefix_appears_bold == "no":
            status = "warning"
            notes.append(
                'The "GOVERNMENT WARNING:" prefix does not appear to be bold. '
                "Bold type is required — please confirm on the physical label."
            )
        elif prefix_appears_bold == "unclear":
            notes.append(
                "Could not confirm bold styling of the prefix from this image "
                "(checked best-effort)."
            )

    return WarningResult(
        status=status,
        extracted_text=extracted_text.strip(),
        expected_text=expected,
        notes=notes,
    )
