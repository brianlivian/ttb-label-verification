# Test data

## Quick samples

- `sample-label-pass.png` — rendered label with brand, class/type, alcohol
  content, net contents, and the full warning with "GOVERNMENT WARNING:" in
  bold caps (expect PASS on the intrinsic checks).
- `sample-label-titlecase-fail.png` — same label but "Government Warning:"
  in title case (expect the warning check to FAIL).

## AI-generated evaluation set (`generated/`)

The main evaluation set: 26 images with ground truth in `manifest.json`.

- **20 AI-generated labels** for fictitious products TTB-101 to TTB-120
  (all present in the base catalog): 9 clean, 8 with deliberately planted
  defects (title-case warning, reworded warning, missing warning, missing
  net contents, ABV contradicting the catalog, missing bottler, missing
  class/type, import without a country-of-origin statement) — plus a beer
  and a table wine without ABV to exercise the beverage-conditional rules.
  On 4 labels the image generator itself garbled the fine print (merged
  words, a dropped heading, a duplicated phrase, a misspelled
  "beveranges"); these are kept and recorded in the manifest as honest
  "render defect" cases — the strict verbatim check caught all of them.
- **6 degraded photo variants** (`-grainy`, `-lowres`, `-blur-dim`,
  `-angled`, `-glare`, `-wrecked` suffixes) simulating the bad photography
  agents actually receive, produced deterministically from the labels above
  so ground truth is unchanged.

Scripts (run from the repo root):

- `python scripts/generate_test_labels.py` — regenerate or extend the
  label set (images that already exist are skipped).
- `python scripts/degrade_test_labels.py` — rebuild the degraded variants.
- `python scripts/score_generated_labels.py <extract-response.json>` —
  grade an `/api/extract` response against the manifest.

The catalog rows for TTB-101..120 hold the *application-of-record* values,
so defects show up as label-vs-catalog disagreements when matching is on —
notably TTB-117, whose label prints 40% ABV while the catalog says 45%.

## Other error cases worth trying

- Any file over 10MB (size limit).
- A `.txt` file renamed to `.jpg` (corrupt/wrong-type handling).
- A non-label image, e.g. a photo of a cat (unreadable-image handling).
- The modify-and-re-upload catalog flow: download the base dataset from the
  UI, change a product's ABV or add a row, re-upload, and check labels
  against your copy.
