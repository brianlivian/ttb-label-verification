"""Record linkage between extracted labels and the product catalog.

Uses LinkTransformer (dell-research-harvard/linktransformer):

- With OPENAI_API_KEY set: `lt.merge_k_judge` — local sentence-transformer
  retrieval of the top-k catalog candidates, then an LLM judge adjudicates
  the match (LinkTransformer's judge supports OpenAI/Gemini only, so this
  step needs an OpenAI key).
- Without it: `lt.merge` — the same local embedding retrieval, with a
  cosine-similarity threshold standing in for the judge. No external calls.

The library (and its first model download) is heavy, so everything is
imported lazily and the matcher runs in a worker thread off the event loop.
"""

import logging
import os
from typing import Optional

import pandas as pd

from app import config
from app.dataset import DATASET_COLUMNS
from app.schemas import MATCH_FIELD_KEYS, LabelResult
from app.schemas import CatalogMatch as MatchResult

logger = logging.getLogger("uvicorn.error")

EMBEDDING_MODEL = os.environ.get(
    "LT_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
# The judge runs on the hosted-scenario model unless explicitly overridden.
JUDGE_LLM_MODEL = os.environ.get("LT_JUDGE_MODEL", "") or config.MODEL_HOSTED
MATCH_K = int(os.environ.get("MATCH_K", "1"))

# Cosine-similarity floor for calling a retrieval-only result a match.
RETRIEVAL_MATCH_THRESHOLD = float(os.environ.get("MATCH_THRESHOLD", "0.80"))

# Matching links on the catalog's four fields; bottler/origin are intrinsic
# checks and don't participate in record linkage.
_ON_COLUMNS = list(MATCH_FIELD_KEYS)


class MatchingUnavailableError(Exception):
    """Raised when LinkTransformer or its model cannot be loaded."""


def _extracted_frame(results: list[LabelResult]) -> pd.DataFrame:
    rows = []
    for index, result in enumerate(results):
        values = {f.field: (f.value or "") for f in result.fields}
        rows.append({"label_index": index, **{c: values.get(c, "") for c in _ON_COLUMNS}})
    return pd.DataFrame(rows)


def _catalog_frame(products: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(products, columns=list(DATASET_COLUMNS)).fillna("")


def _judged_merge(left: pd.DataFrame, catalog: pd.DataFrame, openai_key: str) -> pd.DataFrame:
    import linktransformer as lt

    return lt.merge_k_judge(
        df1=left,
        df2=catalog,
        on=_ON_COLUMNS,
        model=EMBEDDING_MODEL,
        k=MATCH_K,
        llm_provider="openai",
        judge_llm_model=JUDGE_LLM_MODEL,
        openai_key=openai_key,
        confidence_threshold=0.0,
    )


def _retrieval_merge(left: pd.DataFrame, catalog: pd.DataFrame) -> pd.DataFrame:
    import linktransformer as lt

    return lt.merge(left, catalog, on=_ON_COLUMNS, model=EMBEDDING_MODEL, merge_type="1:m")


def match_results(
    results: list[LabelResult],
    products: list[dict],
    use_judge: bool = True,
) -> list[Optional[MatchResult]]:
    """Match each extracted label against the catalog. Synchronous and heavy —
    call via asyncio.to_thread. Returns one entry per result (None for labels
    that errored before extraction)."""
    matchable = [r for r in results if r.verdict != "error" and r.fields]
    if not matchable:
        return [None] * len(results)

    left = _extracted_frame(results)
    left = left[left["label_index"].isin(
        [i for i, r in enumerate(results) if r.verdict != "error" and r.fields]
    )]
    catalog = _catalog_frame(products)

    openai_key = config.OPENAI_API_KEY if use_judge else ""
    method = "llm_judge" if openai_key else "embedding_retrieval"

    try:
        if openai_key:
            merged = _judged_merge(left, catalog, openai_key)
        else:
            merged = _retrieval_merge(left, catalog)
    except ImportError as exc:
        raise MatchingUnavailableError(
            "LinkTransformer is not installed on the server."
        ) from exc
    except Exception as exc:
        logger.exception("Catalog matching failed")
        raise MatchingUnavailableError(
            f"Catalog matching failed: {type(exc).__name__}."
        ) from exc

    matches: list[Optional[MatchResult]] = [None] * len(results)
    for _, row in merged.iterrows():
        index = int(row["label_index"])
        score = float(row["score"]) if "score" in row and pd.notna(row["score"]) else None
        # Overlapping columns get _x (left) / _y (catalog) suffixes; columns
        # unique to one side (product_id, bottler_address, …) keep their
        # names. Carry the FULL catalog row so downstream cross-checks see
        # every catalog field, not just the ones matching linked on.
        product = {
            c: str(row.get(f"{c}_y", row.get(c, "")) or "") for c in DATASET_COLUMNS
        }

        if method == "llm_judge":
            is_match = bool(row.get("llm_is_match", False))
            confidence = row.get("llm_confidence")
            confidence = float(confidence) if pd.notna(confidence) else None
            note = (
                "Match confirmed by LLM judge."
                if is_match
                else "Closest catalog product was rejected by the LLM judge."
            )
        else:
            is_match = score is not None and score >= RETRIEVAL_MATCH_THRESHOLD
            confidence = None
            note = (
                f"Matched by embedding similarity ({score:.2f})."
                if is_match
                else f"No catalog product was similar enough (best {score:.2f}, "
                f"threshold {RETRIEVAL_MATCH_THRESHOLD})."
                if score is not None
                else "No catalog candidate found."
            )

        candidate = MatchResult(
            matched=is_match,
            method=method,
            product=product if is_match else None,
            score=score,
            judge_confidence=confidence,
            note=note,
        )
        # With k > 1 the judge sees several candidates per label — keep the
        # best (matched beats unmatched; then higher confidence/score).
        current = matches[index]
        if current is None or _better(candidate, current):
            matches[index] = candidate

    return matches


def _better(a: MatchResult, b: MatchResult) -> bool:
    if a.matched != b.matched:
        return a.matched
    return (a.judge_confidence or a.score or 0) > (b.judge_confidence or b.score or 0)
