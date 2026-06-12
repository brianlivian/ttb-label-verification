"""Field-level comparison of extracted values against catalog values.

Deterministic code, not a model call: brand and class/type are compared
after normalizing case/punctuation (so "STONE'S THROW" matches "Stone's
Throw"), while alcohol content and net contents are compared numerically so
equivalent formats agree ("45% Alc./Vol." = "45% ABV" = "90 Proof";
"750 mL" = "75 cl" = "0.75 L"). Near-misses become NEEDS REVIEW rather than
FAIL so an agent confirms instead of the tool guessing.
"""

import difflib
import re
from typing import Optional

from app.schemas import LabelResult, Status


def _norm_text(text: str) -> str:
    """Normalize for text comparison: case, punctuation, whitespace.

    Keeps unicode letters (umlauts, accents) — labels are not all ASCII.
    """
    text = text.replace("’", "'").lower()
    text = re.sub(r"[^\w%. ]", " ", text, flags=re.UNICODE)
    text = text.replace("_", " ")
    return re.sub(r"\s+", " ", text).strip()


_PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_PROOF_RE = re.compile(r"(\d+(?:\.\d+)?)\s*proof", re.IGNORECASE)

_VOLUME_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(ml|cl|l|liter|liters|litre|litres|fl\.?\s*oz|oz)\b",
    re.IGNORECASE,
)
_ML_PER_UNIT = {"ml": 1.0, "cl": 10.0, "l": 1000.0, "oz": 29.5735}


def _parse_abv(text: str) -> Optional[float]:
    if match := _PERCENT_RE.search(text):
        return float(match.group(1))
    if match := _PROOF_RE.search(text):
        return float(match.group(1)) / 2.0  # US proof = 2 x ABV
    return None


def _parse_volume_ml(text: str) -> Optional[float]:
    # European decimal commas ("0,04 l") vs thousands separators ("1,000 mL"):
    # a comma followed by 1-2 digits is a decimal point, 3 a separator.
    text = re.sub(r"(\d),(\d{1,2})\b", r"\1.\2", text)
    if match := _VOLUME_RE.search(text.replace(",", "")):
        amount = float(match.group(1))
        unit = re.sub(r"[^a-z]", "", match.group(2).lower())
        if unit in ("floz", "oz"):
            unit = "oz"
        elif unit.startswith("liter") or unit.startswith("litre"):
            unit = "l"
        return amount * _ML_PER_UNIT[unit]
    return None


def _compare_text(extracted: str, expected: str) -> tuple[Status, str]:
    norm_extracted, norm_expected = _norm_text(extracted), _norm_text(expected)
    if norm_extracted == norm_expected:
        if extracted.strip() != expected.strip():
            return "pass", f'Matches the catalog value "{expected}" (formatting differs).'
        return "pass", ""
    similarity = difflib.SequenceMatcher(None, norm_extracted, norm_expected).ratio()
    # Space-stripped containment catches compound-word variants like
    # "Kräuter-Likör" vs "Herbal Liqueur (Kräuterlikör)"; trailing periods
    # are transcription artifacts ("JOHNNIE WALKER.") and must not break it.
    compact_extracted = norm_extracted.replace(" ", "").strip(".")
    compact_expected = norm_expected.replace(" ", "").strip(".")
    if (
        similarity >= 0.85
        or compact_extracted in compact_expected
        or compact_expected in compact_extracted
    ):
        return "warning", f'Similar to the catalog value "{expected}" — please confirm.'
    return "fail", f'Does not match the catalog value "{expected}".'


def _compare_numeric(
    extracted: str, expected: str, parse, tolerance: float, what: str
) -> tuple[Status, str]:
    extracted_n, expected_n = parse(extracted), parse(expected)
    if extracted_n is None or expected_n is None:
        return _compare_text(extracted, expected)
    if abs(extracted_n - expected_n) <= tolerance:
        if _norm_text(extracted) != _norm_text(expected):
            return "pass", f'Same {what} as the catalog ("{expected}"), different format.'
        return "pass", ""
    return "fail", f'{what.capitalize()} differs from the catalog value "{expected}".'


# Bottler lines and country statements rarely match the catalog character
# for character ("Frankfort, KY" vs "Frankfort, Kentucky"; "Product of
# Mexico" vs "Mexico") — similar wording passes with a note instead of
# demanding review.
_LENIENT_TEXT_FIELDS = {"bottler_address", "country_of_origin"}


def compare_field(key: str, extracted: str, expected: str) -> tuple[Status, str]:
    if key == "alcohol_content":
        return _compare_numeric(extracted, expected, _parse_abv, 0.05, "alcohol content")
    if key == "net_contents":
        return _compare_numeric(extracted, expected, _parse_volume_ml, 1.0, "net contents")
    status, note = _compare_text(extracted, expected)
    if key in _LENIENT_TEXT_FIELDS and status == "warning":
        return "pass", f'Consistent with the catalog value "{expected}".'
    return status, note


_SEVERITY = {"pass": 0, "warning": 1, "fail": 2}


def apply_expected(result: LabelResult, expected: Optional[dict[str, str]]) -> None:
    """Fold catalog-value comparison into a label's fields and verdict."""
    if result.verdict == "error":
        return
    if expected is None:
        result.notes.append(
            "No catalog product matched this label, so field values were "
            "not cross-checked."
        )
        return

    for field in result.fields:
        expected_value = expected.get(field.field)
        if not expected_value:
            continue
        field.expected_value = expected_value
        if not field.value:
            note = f'The catalog expects "{expected_value}".'
        else:
            status, note = compare_field(field.field, field.value, expected_value)
            if _SEVERITY[status] > _SEVERITY[field.status]:
                field.status = status
        if note:
            field.notes = f"{field.notes} {note}".strip()

    statuses = [f.status for f in result.fields]
    if result.warning_statement:
        statuses.append(result.warning_statement.status)
    result.verdict = (
        "fail" if "fail" in statuses else "warning" if "warning" in statuses else "pass"
    )
