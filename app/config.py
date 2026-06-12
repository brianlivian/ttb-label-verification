"""Application settings, all driven by environment variables.

Nothing here (or anywhere else in the app) persists data: uploads are held in
memory for the lifetime of a single request and then discarded. That is a
deliberate design choice for a federal prototype handling potentially
sensitive application documents.
"""

import os


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


# API keys are read server-side only and never reach the client. With an
# OpenRouter key, the whole app (vision extraction and the LinkTransformer
# judge) routes through OpenRouter's OpenAI-compatible endpoint; otherwise a
# plain OpenAI key hits api.openai.com.
_OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENAI_API_KEY = _OPENROUTER_KEY or os.environ.get("OPENAI_API_KEY", "")

OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "") or (
    "https://openrouter.ai/api/v1" if _OPENROUTER_KEY else ""
)
if OPENAI_BASE_URL:
    # LinkTransformer constructs its own OpenAI client without a base_url;
    # the SDK falls back to these env vars, so exporting them here routes
    # the judge through the same endpoint and key as the rest of the app.
    os.environ["OPENAI_BASE_URL"] = OPENAI_BASE_URL
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY

# The two demo deployment scenarios, selectable per request in the UI:
# - "open":   an open-weights model — stands in for the federal-laptop /
#             no-commercial-API scenario, since the same weights could be
#             self-hosted on-premises (served via OpenRouter for this demo;
#             the ":free" tier variant works too but congests often)
# - "hosted": a hosted commercial API model — the Azure-deployment scenario
# ":nitro" = OpenRouter's throughput-optimized routing — measured 4.6x
# faster than default routing for the same weights (31.7s -> 6.9s for a
# 3-label batch) with identical extraction results.
MODEL_OPEN = os.environ.get("MODEL_OPEN", "google/gemma-4-26b-a4b-it:nitro")
MODEL_HOSTED = os.environ.get("MODEL_HOSTED", "anthropic/claude-sonnet-4.6")

# For gpt-5-family (reasoning) models only: how much thinking to allow per
# extraction. "low" keeps latency near the mini-tier baseline.
OPENAI_REASONING_EFFORT = os.environ.get("OPENAI_REASONING_EFFORT", "low")

# Per-request timeout for a single vision call, in seconds.
LLM_TIMEOUT_SECONDS = _int_env("LLM_TIMEOUT_SECONDS", 30)

# Upload limits.
MAX_UPLOAD_MB = _int_env("MAX_UPLOAD_MB", 10)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
# Sized for the peak-season scenario from the interviews: importers dumping
# 200-300 label applications at once.
MAX_LABELS_PER_BATCH = _int_env("MAX_LABELS_PER_BATCH", 300)

# How many vision calls may run at once during a batch.
BATCH_CONCURRENCY = _int_env("BATCH_CONCURRENCY", 8)

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
