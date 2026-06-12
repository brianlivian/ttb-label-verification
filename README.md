# TTB Label Extraction Prototype

AI-powered extraction and intrinsic compliance checking of alcohol beverage
label images, built as a take-home prototype for the Compliance Division.

**Live demo:** https://ttb-labels-app.politesand-253a8765.eastus.azurecontainerapps.io
(Azure Container Apps; auto-deployed from `main` by GitHub Actions via OIDC —
every push builds, tests, and ships a new revision tagged by commit SHA.)

An agent uploads one or many label images. For each label the app extracts
the required label elements — brand name, class/type designation, alcohol
content, net contents, bottler/producer name and address, and country of
origin — plus the government health warning statement, runs the checks that
need no application data, and returns a green/red verdict per label in a
few seconds. Results can be downloaded as a styled Excel workbook.

**Checks performed (intrinsic):**

- **Government warning** — present, verbatim match to the federal text
  (27 CFR 16.21), "GOVERNMENT WARNING:" in ALL CAPS (title case = violation),
  bold/prominence reported best-effort.
- **Required elements present** — fields found and readable; missing
  elements fail, uncertain reads are flagged for review. Requiredness is
  beverage-aware, per the TTB regulations (27 CFR parts 4, 5, 7):
  alcohol content is mandatory for distilled spirits (§ 5.65) but optional
  for malt beverages (§ 7.65), and wine at or under 14% ABV may carry a
  "table wine"/"light wine" designation instead of a number (§ 4.36);
  the bottler/producer name-and-address statement is mandatory for all
  (§ 5.66 — role phrase + city + state); country of origin is required only
  when the label appears to be an imported product.
- **Per-label verdict** — PASS / FAIL / NEEDS REVIEW, with notes.

**Catalog matching (record linkage):** every label is matched against a
product catalog — a built-in fictitious dataset of 42 products (22
popular liquors plus the 20 evaluation-set products) that
stands in for the application-of-record system. Reviewers can download the
dataset as Excel, analyze or modify it, add rows, and re-upload their copy
to match against instead. Matching uses
[LinkTransformer](https://github.com/dell-research-harvard/linktransformer):
`merge_k_judge` (local sentence-transformer retrieval + an LLM judge) when
an API key is set, falling back to pure local embedding retrieval with
a similarity threshold otherwise. The matched product's values are then
cross-checked against the extracted fields: equivalent formats agree
("45% Alc./Vol." ≡ "45% ABV" ≡ "90 Proof"; "750 mL" ≡ "75 cl" ≡ "0,04 l" ≡
"0.04 L" for the respective volumes; casing/punctuation/umlaut differences
pass with a note), near-misses become NEEDS REVIEW, and genuine mismatches
fail with both values shown. Labels with no sufficiently similar catalog
product are flagged "no match" rather than force-matched.

## Running it

### Locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # put your real OPENROUTER_API_KEY in .env
export $(grep -v '^#' .env | xargs)
uvicorn app.main:app --port 8080
```

Open http://localhost:8080.

### Docker

```bash
docker build -t ttb-label-extract .
docker run -p 8080:8080 -e OPENROUTER_API_KEY=sk-or-... ttb-label-extract
```

The container listens on `$PORT` (default 8080) and is deployed to Azure
Container Apps. The GitHub Actions workflow runs the tests, builds and
pushes the image to GitHub Container Registry, and deploys the new revision
to Azure on every push to `main` (OIDC federated login — no stored cloud
credentials; commit-SHA image tags make any revision a one-command
rollback).

### Tests

```bash
pip install pytest
python -m pytest tests/ -q
```

## Architecture

```
Browser (vanilla HTML/JS, served by the app — no external requests)
   │  same-origin fetch only
   ▼
FastAPI (app/main.py) ── upload validation, batch orchestration
   ├── app/images.py        decode/validate, downscale, re-encode JPEG
   ├── app/verification.py  one vision call per label → strict JSON
   │                        (structured outputs: schema-enforced response)
   ├── app/warning.py       deterministic government-warning exact check
   ├── app/dataset.py       built-in product catalog + dataset upload parsing
   ├── app/matching.py      LinkTransformer record linkage (merge_k_judge /
   │                        local embedding retrieval fallback)
   ├── app/compare.py       deterministic field comparison vs matched product
   └── app/export.py        in-memory styled .xlsx (openpyxl), streamed
   │
   ▼
OpenRouter (OpenAI-compatible API) — server-side only, key in env var
     open scenario:   Gemma 4 26B (open weights) + local-only matching
     hosted scenario: Claude Sonnet 4.6 (vision + LLM match judge)
```

Each label is one vision call returning, per field,
`{value, present, confidence, notes}` plus a verbatim transcription of the
warning statement. Verdict logic runs in code: a missing required element
fails, a low-confidence read becomes NEEDS REVIEW, and the warning statement
is compared exactly. Batch uploads fan out concurrently (bounded by a
semaphore) against the vision API, so a 20-label batch takes about as long
as the slowest single label, not 20× one label.

## Key decisions and rationale

**All AI calls are server-side.** The IT interview was explicit that the TTB
network blocks outbound traffic to most domains — the previous vendor's
client-side ML calls died at the firewall. The browser here only ever talks
to this app: every fetch is same-origin, and all static assets (CSS, JS,
fonts) are served by the app itself — no CDNs, nothing third-party from the
client. An agent inside the TTB firewall needs exactly one domain
whitelisted. The API key lives in an environment variable and never
appears in client code or the repo.

**Why the prototype's own AI calls don't hit that firewall:** the app is
deployed to Azure Container Apps, *outside* the Treasury network — the only
traffic that crosses the agency boundary is the browser talking to the
app's single domain. The server's outbound calls to AI services happen from
Azure, where the agency firewall doesn't apply. For a production deployment
inside the boundary, those calls swap to enterprise-secure / FedRAMP-
authorized LLM endpoints — for an OpenAI-based stack in a Treasury/Azure
shop, that is Azure OpenAI Service in Azure Government — a
client-configuration change, not a redesign, because every external call is
isolated behind one function.

The app server has exactly two outbound dependencies, each with a known
in-boundary answer:

| Server-side outbound call | When | In-boundary answer |
|---|---|---|
| `openrouter.ai` (vision + match judge) | every extraction | the open scenario's models are open weights — self-host them on agency GPU hardware (zero AI egress); the hosted scenario swaps to a FedRAMP-authorized endpoint. Everything honors `OPENAI_BASE_URL`, so any OpenAI-compatible endpoint is a config change. |
| `huggingface.co` (embedding model) | never in the container (model is pre-baked into the image at build time); only on first match when running outside Docker | already solved — the Dockerfile bakes the model in |

**Stateless by design — nothing is persisted.** Uploads are processed in
memory and discarded when the request ends; there is no database, no file
storage, no logging of document contents. Even the Excel export is built in
memory from results the client echoes back and streamed straight to the
download — nothing is written to disk. For a prototype handling federal
data this sidesteps PII and document-retention obligations entirely (per the
IT interview: "we're not storing anything sensitive for this exercise").

**The government warning verdict is computed in code, not by the model.**
27 CFR 16.21 requires the statement verbatim with "GOVERNMENT WARNING:" in
capitals — the strictest check in the system, and the one applicants game
(title case, reworded text). The vision model is only asked to transcribe
what is printed character-for-character; Python then does the exact
comparison (whitespace/typographic-quote normalization only, title-case
prefix = fail). Bold styling can only be judged best-effort from a photo, so
a "not bold" result downgrades to NEEDS REVIEW rather than FAIL.

**Extraction confidence is surfaced, not hidden.** The model is instructed
never to guess: a value it cannot read confidently comes back with low
confidence and becomes NEEDS REVIEW instead of a silent pass — the right
failure direction for a compliance tool, and the path that handles Jenny's
"weird angles, bad lighting, glare" cases honestly.

**Matching and comparison are separate, swappable layers.** Record linkage
(which catalog product is this label?) uses LinkTransformer — `merge_k_judge`
retrieves the top catalog candidates with a local sentence-transformer and
has an LLM adjudicate the match. LinkTransformer's judge supports
OpenAI-compatible endpoints only, so it runs when an API key is set; without it the
app uses the same local embedding retrieval with a cosine-similarity
threshold (0.80 by default) and labels results "embedding retrieval" vs
"LLM judge" honestly. Field comparison (do the values agree?) stays
deterministic, unit-tested code: numeric equivalence for alcohol content and
net contents (including proof conversion and European decimal commas),
unicode-aware normalized text for brand and class/type, near-misses flagged
for review. This still honors Dave's judgment cases — "STONE'S THROW" vs
"Stone's Throw" passes with a note.

**Two deployment scenarios, one toggle.** Every check runs in one of two
selectable model scenarios, demonstrating the federal deployment trade-off
head-on:

| Scenario | Extraction model | Catalog matching | What it demonstrates |
|---|---|---|---|
| **Open-weights** | Gemma 4 26B (open model) | local embedding retrieval only | the federal-laptop / no-commercial-API case: every model involved has downloadable weights and could run entirely on-premises |
| **Hosted API** | Claude Sonnet 4.6 | LinkTransformer `merge_k_judge` with an LLM judge | the Azure-hosted case where commercial AI APIs are reachable |

For this prototype both scenarios are served through OpenRouter's
OpenAI-compatible endpoint (one key, one egress domain; `MODEL_OPEN` /
`MODEL_HOSTED` env-overridable) — the point of the open scenario is that
nothing in it *requires* a hosted API: the same Gemma weights and the same
sentence-transformer matcher would run on agency GPU hardware in
production. The default uses OpenRouter's ":nitro" throughput routing —
measured 4.6x faster than default marketplace routing for the same weights
(5-7s per label vs 25-32s) with identical extraction output, a concrete
illustration that open-model latency is a serving question, not a weights
question. (A ":free"-tier model also works via `MODEL_OPEN` but congests
often; if one rate-limits, the app falls back to the same model's standard
endpoint automatically and notes it in the results.)
Models that support strict structured outputs get schema-enforced JSON;
those that don't get the schema embedded in the prompt with a tolerant
parser — detected and remembered per model. Vision
detail is set to high so fine print gets enough image tokens; images are
downscaled to ≤1600px and re-encoded before the call. Per-label processing
time is measured and shown with the batch wall-clock total.

**Excel export for the real workflow.** Agents live in spreadsheets; batch
results stream as a styled .xlsx — one row per label, color-coded verdicts,
auto-width columns — generated server-side with openpyxl and never touching
disk.

**UI built for the actual users.** Half the team is over 50 and the
benchmark user is Sarah's 73-year-old mother: one linear two-step flow
(upload → results), serif 19px base type, large high-contrast buttons,
plain-language errors, and verdicts as big green/red badges ("PASS" /
"FAIL" / "NEEDS REVIEW") rather than icons alone. Batch results show a
summary table with per-label drill-down.

## Evaluation

The repo includes a controlled evaluation set
(`test-data/generated/`, ground truth in its `manifest.json`): 26 images —
20 AI-generated labels for fictitious products in the catalog (8 with
deliberately planted defects: title-case warning, reworded warning, missing
warning, missing net contents, ABV contradicting the catalog, missing
bottler, missing class/type, import without a country-of-origin statement)
plus 6 degraded photo variants (grain, low-res JPEG, motion blur + dim,
perspective angle, glare, near-illegible). Score any run with
`python scripts/score_generated_labels.py <extract-response.json>`.

Results on the full 26-image set (both scenarios, matching on and off):

| Scenario | Catalog matching | Verdict accuracy | Processing per label |
|---|---|---|---|
| Hosted (Claude Sonnet 4.6) | off | 26/26 | 1.4s |
| Hosted (Claude Sonnet 4.6 + LLM judge) | on | 26/26 | 3.2s |
| Open (Gemma 4 26B) | off | 24/26 | 0.8s |
| Open (Gemma 4 26B, local matching) | on | 26/26 | 1.2s |

Per-label times are batch-amortized (8 labels process concurrently).
Record linkage matched every label to its correct catalog product in both
modes (similarity 0.83-1.0), including labels missing the very fields the
catalog supplied — which is what let the cross-check catch the planted
ABV-contradiction (label 40% vs catalog 45%) that no intrinsic check can
see.

Two findings worth highlighting:

- **The strict verbatim check caught four defects nobody planted.** The
  image generator itself garbled fine print on four labels (merged words, a
  dropped heading, a duplicated phrase, a misspelled "beveranges") — all
  caught and verified against the source images, then recorded in the
  manifest as render defects.
- **Transcription fidelity is the differentiator, and it favors the
  stronger model.** Sonnet transcribed character-perfectly in every run,
  including faithfully reproducing the misprints above. The open model is
  3-4x faster and cheaper but varies run to run (92-100% across repeats):
  it occasionally normalizes what it reads (capitalizing a title-case
  prefix, silently fixing a misprint) or adds artifacts — each a way a
  verbatim check can be silently defeated or false-triggered. For a
  compliance tool the literal transcriber wins; the open scenario remains
  the right shape where no external API is permissible.
- One documented blind spot: the warning normalizer strips asterisks
  because vision models use `**` to denote bold type — so a label that
  genuinely *printed* asterisks around the prefix (one render did) passes
  the wording check and is flagged only by the best-effort bold note.
  Printed asterisks and transcription markup are indistinguishable in text.

## Error handling

| Case | Behavior |
|---|---|
| Wrong file type | Rejected client-side and server-side with a plain-language message |
| File over 10MB | Rejected with the limit stated |
| Corrupt/unreadable image | Pillow decode fails → per-label "could not be read" result; other labels in the batch still complete |
| Image that isn't a label | Model reports illegible → per-label explanation |
| AI API failure or timeout | Per-label error result (batch continues); SDK retries transient 429/5xx once |
| Misconfiguration (bad key, no credits, rate limit) | Specific actionable message instead of a generic "try again"; details logged server-side |
| Empty batch / empty file | Caught in the UI and again server-side (400) |

## Assumptions

- **Interpretation of the firewall constraint (cloud AI APIs).** The IT
  interview notes warn that the TTB network "blocks outbound traffic to a
  lot of domains … keep that in mind if you're thinking about cloud APIs,"
  and describe the prior vendor's failure: software running *inside* the
  agency network calling out to ML endpoints. This prototype interprets
  that as a constraint on traffic originating inside the TTB network — so
  the client makes zero third-party calls (every browser request is
  same-origin, all assets self-hosted), and all AI calls originate from the
  app's own host, which runs outside the agency network. An agent behind
  the TTB firewall needs exactly one domain reachable: the app's.
  If the stricter intent was "no cloud AI services at all," the extraction
  backend is isolated behind a single function (`app/verification.py`) and
  swaps to a self-hosted open vision model on in-boundary GPU
  infrastructure; catalog matching already runs on a fully local,
  self-hosted embedding model. That swap carries a documented cost — on
  CPU-only hosting a capable open vision model takes 30+ seconds per label,
  the exact latency that killed the agency's previous scanning pilot, so it
  is a deliberate non-choice for this prototype rather than an oversight.
- The extracted elements are the seven "common elements" listed in the
  assignment (brand, class/type, alcohol content, net contents, bottler
  name/address, country of origin for imports, health warning). Remaining
  conditional Part 4/5/7 disclosures (sulfites, FD&C Yellow No. 5, age
  statements, state of distillation, …) are follow-on work — the per-field
  result model extends directly.
- Whether a label is an import (driving the country-of-origin requirement)
  is judged by the vision model from label evidence ("Product of …",
  "Imported by …", a foreign producer address); catalog matching and
  comparison cover the four fields the catalog models.
- The mandatory warning text used is the 27 CFR 16.21 statement; it is a
  constant in `app/warning.py` if it ever changes.
- English-language labels.

## Trade-offs and limitations

- **The product catalog is a fictitious stand-in** — in production this tool
  would pull the application record from COLAs Online and match/compare
  against it, which is the natural integration point. The catalog layer
  (`app/dataset.py`) is already separated from matching and comparison for
  exactly that swap.
- **The whole stack speaks one OpenAI-compatible API** (OpenRouter by
  default, since LinkTransformer's `merge_k_judge` judge supports
  OpenAI-compatible endpoints) — one key covers vision extraction and match
  adjudication for both scenarios, and `OPENAI_BASE_URL` retargets
  everything at once. If the key is ever absent, extraction refuses with a
  clear message and matching falls back to local embedding retrieval with a
  0.80 similarity threshold (same retrieval, no adjudication), with the
  method labeled in the results and Excel export.
- **LinkTransformer is a heavy dependency** (PyTorch + sentence-transformers,
  ~2GB installed, so the container image is large). The embedding model is
  pre-baked into the Docker image at build time, so containers make no
  Hugging Face calls at runtime; running outside Docker downloads it once on
  first match and caches it.
- **Bold/prominence detection is best-effort.** Type weight from a photo is
  genuinely ambiguous; the app reports it as a review note instead of
  pretending certainty.
- **No font-size/contrast (legibility) compliance checks** — out of scope.
- **Batch size allows the peak-season scenario (default 300 per
  request)** — the interviews describe importers dumping 200-300 label
  applications at once. Labels process concurrently (8 at a time), so a
  300-label batch takes roughly 4-5 minutes of wall clock in one request;
  a production version would queue with progress reporting instead of
  holding one HTTP request open.
- **Per-label cost** is zero-to-fractions of a cent per scenario; no caching or
  batching-API cost optimization was added at prototype scale.
- **Extraction accuracy is bounded by image quality.** The model is
  instructed to mark uncertain reads as low confidence rather than guessing,
  which trades some false "needs review" for fewer false passes.

## Path to production

- **Hosting/compliance:** deploy to Azure Government / a FedRAMP-authorized
  boundary (TTB is already on Azure). The container is platform-agnostic;
  the work is in the ATO paperwork, not the code.
- **In-boundary inference:** the open scenario's models self-host
  on agency GPU hardware (zero external AI calls); the hosted scenario
  swaps OpenRouter for a FedRAMP-authorized endpoint (e.g. Azure OpenAI in
  Azure Government) so
  label data never leaves the boundary. The vision-call surface is isolated
  in one function, so this is a configuration change.
- **AuthN/AuthZ:** front with Microsoft Entra ID (the agency identity
  provider) — agents sign in with their existing accounts; role-based access
  if supervisors get review queues.
- **Audit logging:** append-only log of who checked what and the outcome
  (required for federal systems), with image *contents* still excluded from
  logs per retention policy.
- **PII/retention:** if results must be saved, define a retention schedule
  with records management first; the stateless core makes "storage" an
  explicit, reviewable addition rather than an accident.
- **COLA integration:** the natural next step is pulling the application of
  record from COLAs Online by application ID and cross-validating it against
  these extracted fields — the extraction results are already structured for
  exactly that comparison.
- **Scale/ops:** queue + worker pool for the 300-label peak-season dumps,
  rate-limit handling per org quota, health/metrics endpoints, and an
  evaluation set of known-good/known-bad labels to regression-test prompt or
  model changes.

## Tools used

Python 3.12, FastAPI, Pillow, openpyxl, pandas, OpenAI Python SDK via
OpenRouter (Gemma 4 / Claude Sonnet vision extraction), LinkTransformer
(dell-research-harvard — record linkage via `merge_k_judge` / embedding
retrieval), vanilla HTML/CSS/JS, Docker, GitHub Actions. AI test labels per
`test-data/README.md`.
