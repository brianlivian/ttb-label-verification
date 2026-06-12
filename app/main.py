"""FastAPI application: routes, upload validation, batch orchestration.

All third-party (OpenAI API) calls happen here on the server — the browser
only ever talks to this app. That is a hard requirement: the TTB network
blocks outbound traffic to most external domains, so a frontend that called
an AI API directly would silently fail inside their firewall.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

import openai
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from app import config
from app.compare import apply_expected
from app.dataset import (
    BASE_PRODUCTS,
    DatasetFileError,
    expected_values,
    parse_dataset_file,
)
from app.export import build_dataset_workbook, build_workbook
from app.images import UnreadableImageError, prepare_image
from app.matching import MatchingUnavailableError, match_results
from app.schemas import ExportRequest, ExtractResponse, LabelResult
from app.verification import extract_label

app = FastAPI(title="TTB Label Extraction Prototype", docs_url=None, redoc_url=None)

STATIC_DIR = Path(__file__).parent / "static"

logger = logging.getLogger("uvicorn.error")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Always return JSON with a `detail` the UI can display.

    FastAPI's default for unhandled exceptions is a plain-text 500, which the
    frontend can only render as a generic "try again" — log the traceback and
    keep the response shape consistent instead.
    """
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Something went wrong on the server. Please try again "
            "— if it keeps happening, an administrator can check the server "
            "log for details."
        },
    )


def _ai_error_message(exc: openai.APIError) -> str:
    """Turn an AI-service failure into an actionable, plain-language message.

    Configuration problems (bad key, empty credit balance) would otherwise
    look identical to transient failures and send the user into a useless
    retry loop.
    """
    if isinstance(exc, openai.AuthenticationError):
        return (
            "The AI service rejected the server's API key. An administrator "
            "needs to check the OPENAI_API_KEY setting."
        )
    if "quota" in str(exc).lower() or "billing" in str(exc).lower():
        return (
            "The AI service account is out of credits. An administrator "
            "needs to add credits before extraction can run."
        )
    if isinstance(exc, openai.RateLimitError):
        return (
            "The AI service is temporarily rate-limiting requests. Please "
            "wait a moment and try again."
        )
    return "The AI service returned an error. Please try again."


async def _read_upload(upload: UploadFile) -> bytes:
    """Read an upload into memory, enforcing the size limit as we go."""
    data = await upload.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Image '{upload.filename}' is larger than "
            f"{config.MAX_UPLOAD_MB}MB. Please upload a smaller file.",
        )
    if not data:
        raise HTTPException(
            status_code=400, detail=f"Image '{upload.filename}' is empty."
        )
    return data


def _validate_image_upload(upload: UploadFile) -> None:
    suffix = Path(upload.filename or "").suffix.lower()
    if (
        upload.content_type not in config.ALLOWED_IMAGE_TYPES
        and suffix not in config.ALLOWED_IMAGE_EXTENSIONS
    ):
        raise HTTPException(
            status_code=400,
            detail=f"'{upload.filename}' is not a supported image. Please "
            "upload JPEG, PNG, WebP, or GIF label images.",
        )


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "api_key_configured": bool(config.OPENAI_API_KEY)}


@app.get("/api/dataset")
async def dataset() -> Response:
    """The base product catalog as a styled Excel download."""
    return Response(
        content=build_dataset_workbook(BASE_PRODUCTS),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="base-product-dataset.xlsx"'
        },
    )


@app.post("/api/extract", response_model=ExtractResponse)
async def extract(
    labels: list[UploadFile] = File(...),
    dataset: UploadFile | None = File(None),
    match_catalog: bool = Form(True),
    model_mode: str = Form("hosted"),
) -> ExtractResponse:
    """Extract one or more label images. With match_catalog on (default),
    each label is then matched against the product catalog (the built-in
    base dataset, or an uploaded modified copy) and cross-checked against
    the matched product's values; with it off, the intrinsic checks alone
    decide the verdict."""
    if not config.OPENAI_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="The server has no OPENROUTER_API_KEY (or OPENAI_API_KEY) "
            "configured. Start it with the .env loaded "
            "(set -a; source .env; set +a) and try again.",
        )
    if model_mode not in ("open", "hosted"):
        raise HTTPException(status_code=400, detail="Unknown model scenario.")
    # "open" = open-weights model (self-hostable, the federal-laptop
    # scenario); "hosted" = commercial API model (the Azure scenario).
    model = config.MODEL_OPEN if model_mode == "open" else config.MODEL_HOSTED

    if not labels:
        raise HTTPException(
            status_code=400, detail="Please add at least one label image."
        )
    if len(labels) > config.MAX_LABELS_PER_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Please upload at most {config.MAX_LABELS_PER_BATCH} "
            "labels per batch.",
        )

    # Validate and read everything up front so a bad file fails the request
    # with a clear message before any AI calls are spent.
    products = BASE_PRODUCTS
    if dataset is not None and dataset.filename:
        try:
            products = parse_dataset_file(await _read_upload(dataset), dataset.filename)
        except DatasetFileError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    uploads: list[tuple[str, bytes]] = []
    for upload in labels:
        _validate_image_upload(upload)
        uploads.append((upload.filename or "label", await _read_upload(upload)))

    semaphore = asyncio.Semaphore(config.BATCH_CONCURRENCY)

    async def run_one(filename: str, raw: bytes) -> LabelResult:
        try:
            image_bytes, media_type = prepare_image(raw)
        except UnreadableImageError as exc:
            return LabelResult(filename=filename, verdict="error", error=str(exc))
        try:
            async with semaphore:
                # Time actual work only — not time spent queued behind the
                # concurrency limit, which isn't this label's processing.
                started = time.monotonic()
                try:
                    result = await extract_label(
                        filename, image_bytes, media_type, model
                    )
                except openai.RateLimitError:
                    if not model.endswith(":free"):
                        raise
                    # Free-tier pools share capacity and congest — fall back
                    # to the SAME open-weights model on its standard
                    # endpoint so the scenario demo doesn't depend on luck.
                    result = await extract_label(
                        filename, image_bytes, media_type, model.removesuffix(":free")
                    )
                    result.notes.append(
                        "The free-tier endpoint was congested; the same "
                        "open-weights model was used via its standard "
                        "endpoint."
                    )
        except openai.APITimeoutError:
            return LabelResult(
                filename=filename,
                verdict="error",
                error="Extraction timed out for this label. Please try again.",
            )
        except openai.APIError as exc:
            logger.error(
                "Extraction failed for %r: %s: %s", filename, type(exc).__name__, exc
            )
            return LabelResult(
                filename=filename, verdict="error", error=_ai_error_message(exc)
            )
        except Exception:
            # One bad label must never take down the whole batch.
            logger.exception("Unexpected error while checking %r", filename)
            return LabelResult(
                filename=filename,
                verdict="error",
                error="An unexpected error occurred while checking this "
                "label. Please try it again.",
            )
        result.processing_seconds = time.monotonic() - started
        return result

    batch_started = time.monotonic()
    results = list(
        await asyncio.gather(*(run_one(name, raw) for name, raw in uploads))
    )

    if not match_catalog:
        # Intrinsic checks only — no record linkage, no catalog cross-check.
        return ExtractResponse(
            results=results,
            catalog=[],
            total_seconds=time.monotonic() - batch_started,
        )

    # Record linkage: match each extracted label to a catalog product
    # (LinkTransformer), then cross-check the matched product's values.
    # Matching is heavy and synchronous, so it runs off the event loop.
    try:
        # The LLM judge is a hosted-API feature; in the open-weights
        # scenario matching stays fully local (embedding retrieval).
        matches = await asyncio.to_thread(
            match_results, results, products, model_mode == "hosted"
        )
    except MatchingUnavailableError as exc:
        for result in results:
            if result.verdict != "error":
                result.notes.append(
                    f"Catalog matching was skipped: {exc} Extracted values "
                    "are shown without a catalog cross-check."
                )
    else:
        for result, match in zip(results, matches):
            if match is None:
                continue
            result.match = match
            apply_expected(
                result, expected_values(match.product) if match.matched else None
            )

    return ExtractResponse(
        results=results,
        catalog=products,
        total_seconds=time.monotonic() - batch_started,
    )


@app.post("/api/export")
async def export(
    payload: str = Form(...),
    labels: list[UploadFile] | None = File(None),
) -> Response:
    """Build a styled .xlsx from the client's results, entirely in memory.

    The client echoes the results JSON and re-sends the label images so the
    (stateless) server can embed linked thumbnails in the workbook.
    """
    try:
        request = ExportRequest(**json.loads(payload))
    except (json.JSONDecodeError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid export payload.")
    if not request.results:
        raise HTTPException(status_code=400, detail="No results to export.")

    images: dict[str, bytes] = {}
    for upload in labels or []:
        if upload.filename:
            images[upload.filename] = await _read_upload(upload)

    workbook = build_workbook(
        request.results,
        request.catalog,
        request.total_seconds,
        images,
        request.client_seconds,
    )
    return Response(
        content=workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": 'attachment; filename="label-extraction-results.xlsx"'
        },
    )


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
