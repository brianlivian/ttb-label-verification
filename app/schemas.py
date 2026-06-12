"""Pydantic models shared by the API layer and the extraction logic."""

from typing import Literal, Optional

from pydantic import BaseModel

# The label elements this prototype extracts and checks (27 CFR parts 4, 5,
# 7 mandatory information plus conditional country of origin for imports).
FIELD_KEYS = (
    "brand_name",
    "class_type",
    "alcohol_content",
    "net_contents",
    "bottler_address",
    "country_of_origin",
)

FIELD_LABELS = {
    "brand_name": "Brand Name",
    "class_type": "Class / Type",
    "alcohol_content": "Alcohol Content",
    "net_contents": "Net Contents",
    "bottler_address": "Bottler Name & Address",
    "country_of_origin": "Country of Origin",
}

# The subset cross-checked against the product catalog (the catalog models
# application data for matching; bottler/origin checks are intrinsic).
MATCH_FIELD_KEYS = ("brand_name", "class_type", "alcohol_content", "net_contents")

Status = Literal["pass", "fail", "warning"]
Confidence = Literal["high", "medium", "low"]


class FieldResult(BaseModel):
    field: str
    label: str
    value: Optional[str]
    present: bool
    confidence: Confidence
    status: Status
    notes: str = ""
    # Set only when an expected-values file was uploaded and had this field.
    expected_value: Optional[str] = None


class WarningResult(BaseModel):
    status: Status
    extracted_text: Optional[str]
    expected_text: str
    notes: list[str] = []


class CatalogMatch(BaseModel):
    """Outcome of record linkage against the product catalog."""

    matched: bool
    method: str  # "llm_judge" or "embedding_retrieval"
    product: Optional[dict] = None
    score: Optional[float] = None
    judge_confidence: Optional[float] = None
    note: str = ""


class LabelResult(BaseModel):
    filename: str
    verdict: Literal["pass", "fail", "warning", "error"]
    fields: list[FieldResult] = []
    warning_statement: Optional[WarningResult] = None
    match: Optional[CatalogMatch] = None
    processing_seconds: Optional[float] = None
    notes: list[str] = []
    error: Optional[str] = None


class ExtractResponse(BaseModel):
    results: list[LabelResult]
    # The catalog the labels were matched against, echoed to the client so
    # the (stateless) export endpoint can reproduce it as the first sheet.
    catalog: list[dict] = []
    # Wall-clock time for the whole batch. Labels run concurrently, so
    # per-label times overlap and don't sum to this.
    total_seconds: Optional[float] = None


class ExportRequest(BaseModel):
    """Results echoed back by the client for Excel export.

    The app is stateless, so the export endpoint regenerates the spreadsheet
    from the results payload rather than from anything stored server-side.
    """

    results: list[LabelResult]
    catalog: list[dict] = []
    total_seconds: Optional[float] = None
    # End-to-end request time measured by the browser (includes uploading
    # the images), as opposed to server-side processing time above.
    client_seconds: Optional[float] = None
