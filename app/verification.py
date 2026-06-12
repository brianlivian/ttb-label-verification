"""Label extraction: one vision call per label, strict JSON out.

The model transcribes what is printed on the label — the four required
elements plus a verbatim transcription of the government warning statement —
and reports a confidence for each. All verdict logic happens in code:
missing required elements fail, low-confidence reads need review, and the
government warning is checked exactly (see app.warning).
"""

import base64
import json

import openai

from app import config
from app.schemas import (
    FIELD_KEYS,
    FIELD_LABELS,
    FieldResult,
    LabelResult,
    Status,
)
from app.warning import check_government_warning

# A single shared async client, created lazily — the SDK constructor raises
# without a key, and the app must still boot (and serve its friendly
# missing-key message) when the env var is absent. The SDK handles its own
# connection pooling and retries (429/5xx) with backoff.
_client: openai.AsyncOpenAI | None = None


def _get_client() -> openai.AsyncOpenAI:
    global _client
    if _client is None:
        _client = openai.AsyncOpenAI(
            api_key=config.OPENAI_API_KEY or "missing-key",
            base_url=config.OPENAI_BASE_URL or None,
            timeout=float(config.LLM_TIMEOUT_SECONDS),
            max_retries=1,
        )
    return _client

_NULLABLE_STRING = {"type": ["string", "null"]}

_FIELD_SCHEMA = {
    "type": "object",
    "properties": {
        "value": _NULLABLE_STRING,
        "present": {"type": "boolean"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": "string"},
    },
    "required": ["value", "present", "confidence", "notes"],
    "additionalProperties": False,
}

# Structured-output schema: the API guarantees the response parses against
# this, so there is no free-text parsing or retry-on-bad-JSON logic needed.
EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "legible": {"type": "boolean"},
        "legibility_notes": {"type": "string"},
        "appears_imported": {"type": "boolean"},
        "fields": {
            "type": "object",
            "properties": {key: _FIELD_SCHEMA for key in FIELD_KEYS},
            "required": list(FIELD_KEYS),
            "additionalProperties": False,
        },
        "warning_statement": {
            "type": "object",
            "properties": {
                "present": {"type": "boolean"},
                "verbatim_text": _NULLABLE_STRING,
                "prefix_appears_bold": {
                    "type": "string",
                    "enum": ["yes", "no", "unclear"],
                },
            },
            "required": ["present", "verbatim_text", "prefix_appears_bold"],
            "additionalProperties": False,
        },
    },
    "required": [
        "legible",
        "legibility_notes",
        "appears_imported",
        "fields",
        "warning_statement",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You are assisting a TTB (Alcohol and Tobacco Tax and Trade Bureau) compliance
agent. You read alcohol beverage label images and extract the required label
elements exactly as printed.

Extract these six elements:
- brand_name: the brand name (e.g. "OLD TOM DISTILLERY")
- class_type: the class/type designation (e.g. "Kentucky Straight Bourbon
  Whiskey", "Tequila Blanco", "India Pale Ale", "Cabernet Sauvignon")
- alcohol_content: the alcohol content statement as printed (e.g.
  "45% Alc./Vol. (90 Proof)", "40% ABV", "13.5% Alc. by Vol.")
- net_contents: the net contents as printed (e.g. "750 mL", "12 FL OZ")
- bottler_address: the name-and-address statement of the bottler, distiller,
  producer, or importer, exactly as printed — these start with a role phrase
  such as "Bottled by", "Distilled by", "Produced by", "Brewed by", or
  "Imported by", followed by a company name and a city/state or location
  (e.g. "Distilled and Bottled by Old Tom Distillery, Bardstown, KY")
- country_of_origin: an EXPLICIT country-of-origin statement if printed
  (e.g. "Product of Mexico", "Hecho en Mexico", "Product of Scotland").
  Do NOT infer it from the bottler's address or anything else — if no
  explicit statement is printed, it is null even when the producer is
  clearly foreign.

Also set appears_imported: true when the label indicates a foreign product —
a "Product of [country]" statement, an "Imported by" phrase, a foreign
producer address, or text in a foreign language consistent with an imported
product. Set it false for products that appear to be made in the USA.

For each element:
- value: the text exactly as printed on the label; null if not found.
- present: false only if the element does not appear on the label.
- confidence: "high" when clearly readable; "medium" when readable but
  partially stylized/ambiguous; "low" when glare, angle, blur, or occlusion
  makes the reading uncertain. Never guess — if you cannot read it, mark it
  not present or low confidence and say why.
- notes: one short sentence when something is worth flagging, else "".

Government warning transcription:
- Transcribe the government health warning statement EXACTLY as printed —
  preserve capitalization, punctuation, and numbering character for
  character. Do not correct or normalize it; an exact-match check runs on
  your transcription afterwards.
- Include the opening "GOVERNMENT WARNING:" phrase exactly as printed, even
  when it is styled as a separate heading line above the paragraph.
- Output plain text only — never markdown formatting marks like ** or _ to
  represent bold or italics.
- Report whether the "GOVERNMENT WARNING" prefix appears to be printed in
  bold type ("yes", "no", or "unclear").

If the image is not a readable alcohol beverage label (wrong subject, too
blurry to read anything, etc.), set legible to false and explain in
legibility_notes."""

USER_TEXT = "Extract the required label elements from this label image."


# Beverage classification from the class/type text — requiredness of some
# elements varies by beverage type (27 CFR parts 4, 5, 7).
_MALT_KEYWORDS = (
    "beer", "ale", "lager", "stout", "porter", "pilsner", "ipa", "malt",
    "hefeweizen", "kolsch", "kölsch", "saison",
)
_WINE_KEYWORDS = (
    "wine", "champagne", "sparkling", "port", "sherry", "vermouth", "mead",
    "sake", "cider", "sauvignon", "chardonnay", "merlot", "riesling", "rosé",
    "rose wine", "pinot",
)


def _beverage_type(class_type: str | None) -> str:
    text = (class_type or "").lower()
    if any(k in text for k in _MALT_KEYWORDS):
        return "malt"
    if any(k in text for k in _WINE_KEYWORDS):
        return "wine"
    # Unknown defaults to the strictest (distilled spirits) rules.
    return "spirits"


def _field_status(
    key: str,
    present: bool,
    value: str | None,
    confidence: str,
    beverage: str,
    class_type: str | None,
    appears_imported: bool,
) -> tuple[Status, str]:
    """Required-element check with beverage-aware conditional rules."""
    if present and value:
        if confidence == "low":
            return "warning", ""
        return "pass", ""

    # Missing — is this element actually required for this label?
    if key == "country_of_origin":
        if appears_imported:
            return "fail", (
                "The label appears to be an imported product, so a country "
                "of origin statement is required."
            )
        return "pass", "Not required — the label appears to be a domestic product."

    if key == "alcohol_content":
        if beverage == "malt":
            return "pass", (
                "Alcohol content is optional on malt beverage labels under "
                "federal rules (27 CFR 7.65)."
            )
        if beverage == "wine":
            designation = (class_type or "").lower()
            if "table wine" in designation or "light wine" in designation:
                return "pass", (
                    'The "table wine"/"light wine" designation may stand in '
                    "for a numeric alcohol content at 14% ABV or less "
                    "(27 CFR 4.36)."
                )
            return "warning", (
                "No alcohol content found. Wine at or under 14% ABV may omit "
                'it only with a "table wine"/"light wine" designation '
                "(27 CFR 4.36) — please verify."
            )

    if key == "bottler_address":
        return "fail", (
            "No bottler/producer name-and-address statement was found "
            "(required by 27 CFR 5.66/4.35/7.66 — e.g. \"Bottled by …, "
            "City, State\"). It may be on another label panel."
        )

    return "fail", f"{FIELD_LABELS[key]} was not found on the label."


# Models observed to reject or ignore strict structured outputs (e.g. the
# open-weights Gemma): remembered per process so later labels skip the
# failed strict attempt.
_strict_unsupported: set[str] = set()

_STRICT_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "label_extraction",
        "strict": True,
        "schema": EXTRACTION_SCHEMA,
    },
}

_FALLBACK_JSON_INSTRUCTION = (
    "\n\nRespond with ONLY a single JSON object — no markdown fences, no "
    "commentary — that conforms exactly to this JSON schema:\n"
    + json.dumps(EXTRACTION_SCHEMA)
)


# Explicit country-of-origin statements carry a marker phrase; a bare
# country name lifted from the producer's address is not a statement.
_COUNTRY_MARKERS = ("product of", "produce of", "hecho en", "made in", "imported")


def _drop_inferred_country(analysis: dict) -> None:
    """Models keep inferring a country from the bottler address despite
    prompt instructions — if the extracted value is just a country name
    that also appears in the address, it isn't an explicit statement."""
    country = analysis["fields"]["country_of_origin"]
    value = (country["value"] or "").strip()
    if not value or any(marker in value.lower() for marker in _COUNTRY_MARKERS):
        return
    bottler = (analysis["fields"]["bottler_address"]["value"] or "").lower()
    if value.lower() in bottler:
        country["value"] = None
        country["present"] = False
        note = (
            "Only the producer address names a country; no explicit "
            "country-of-origin statement is printed."
        )
        country["notes"] = f"{country['notes']} {note}".strip()


def _parse_analysis(content: str | None, model: str) -> dict:
    if not content:
        raise ValueError(f"{model} returned no content")
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    analysis = json.loads(text)
    for required in ("legible", "fields", "warning_statement", "appears_imported"):
        if required not in analysis:
            raise ValueError(f"{model} response is missing the '{required}' key")
    return analysis


async def _call_model(model: str, content: list) -> str | None:
    # gpt-5-family models are reasoning models: reasoning tokens draw from
    # the completion budget, and the effort knob trades latency for depth
    # (transcription needs pixels, not deep reasoning — keep it low).
    extra = {"reasoning_effort": config.OPENAI_REASONING_EFFORT} if "gpt-5" in model else {}
    response = await _get_client().chat.completions.create(
        model=model,
        max_completion_tokens=6000,
        **extra,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        # Strict structured outputs when the model supports them: the API
        # then guarantees the response parses against the schema. Models
        # that don't get the schema spelled out in the prompt instead.
        **(
            {"response_format": _STRICT_FORMAT}
            if model not in _strict_unsupported
            else {}
        ),
    )
    return response.choices[0].message.content


async def extract_label(
    filename: str,
    image_bytes: bytes,
    media_type: str,
    model: str,
) -> LabelResult:
    """Run a single label through the vision model and assemble the verdict."""
    data_url = (
        f"data:{media_type};base64,"
        + base64.standard_b64encode(image_bytes).decode()
    )
    image_part = {
        "type": "image_url",
        # detail=high: fine print (the warning statement) needs the extra
        # image tokens.
        "image_url": {"url": data_url, "detail": "high"},
    }

    try:
        content = await _call_model(
            model, [image_part, {"type": "text", "text": USER_TEXT}]
        )
        analysis = _parse_analysis(content, model)
    except (openai.BadRequestError, ValueError, json.JSONDecodeError):
        if model in _strict_unsupported:
            raise
        # The model rejected or ignored strict structured outputs — retry
        # once with the schema embedded in the prompt instead.
        _strict_unsupported.add(model)
        content = await _call_model(
            model,
            [image_part, {"type": "text", "text": USER_TEXT + _FALLBACK_JSON_INSTRUCTION}],
        )
        analysis = _parse_analysis(content, model)

    if not analysis["legible"]:
        return LabelResult(
            filename=filename,
            verdict="error",
            error=analysis["legibility_notes"]
            or "The image could not be read as an alcohol beverage label. "
            "Please upload a clearer image.",
        )

    _drop_inferred_country(analysis)
    class_type_value = analysis["fields"]["class_type"]["value"]
    beverage = _beverage_type(class_type_value)
    appears_imported = analysis["appears_imported"]

    fields = []
    for key in FIELD_KEYS:
        raw = analysis["fields"][key]
        status, rule_note = _field_status(
            key,
            raw["present"],
            raw["value"],
            raw["confidence"],
            beverage,
            class_type_value,
            appears_imported,
        )
        notes = " ".join(part for part in (raw["notes"], rule_note) if part).strip()
        fields.append(
            FieldResult(
                field=key,
                label=FIELD_LABELS[key],
                value=raw["value"],
                present=raw["present"],
                confidence=raw["confidence"],
                status=status,
                notes=notes,
            )
        )

    ws = analysis["warning_statement"]
    warning = check_government_warning(
        ws["verbatim_text"] if ws["present"] else None,
        ws["prefix_appears_bold"],
    )

    statuses = [f.status for f in fields] + [warning.status]
    verdict = "fail" if "fail" in statuses else "warning" if "warning" in statuses else "pass"

    return LabelResult(
        filename=filename,
        verdict=verdict,
        fields=fields,
        warning_statement=warning,
    )
