#!/usr/bin/env python3
"""Score an /api/extract results JSON against the generated-labels manifest.

Usage:
    python scripts/score_generated_labels.py /tmp/results.json
where results.json is the response body of POST /api/extract run over the
images in test-data/generated/.
"""

import json
import sys
from pathlib import Path

MANIFEST = Path("test-data/generated/manifest.json")


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1

    manifest = {m["filename"]: m for m in json.loads(MANIFEST.read_text())}
    data = json.loads(Path(sys.argv[1]).read_text())

    agree = 0
    rows = []
    for result in data["results"]:
        entry = manifest.get(result["filename"])
        if entry is None:
            continue
        expected = entry["expected_verdict"]
        actual = "fail" if result["verdict"] == "error" else result["verdict"]
        ok = actual == expected or (expected == "fail" and actual == "warning")
        agree += ok
        reasons = [f["label"] for f in result["fields"] if f["status"] == "fail"]
        if result.get("warning_statement") and result["warning_statement"]["status"] == "fail":
            reasons.append("Warning")
        rows.append((
            entry["product_id"], entry["defect"][:48], expected,
            result["verdict"], "ok" if ok else "MISS", ", ".join(reasons)[:40],
        ))

    print(f"{agree}/{len(rows)} verdicts match ground truth "
          f"(batch {data.get('total_seconds', 0):.0f}s)\n")
    print(f"{'ID':9}{'planted defect':50}{'exp':6}{'got':9}{'':6}reasons")
    for row in sorted(rows):
        print(f"{row[0]:9}{row[1]:50}{row[2]:6}{row[3]:9}{row[4]:6}{row[5]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
