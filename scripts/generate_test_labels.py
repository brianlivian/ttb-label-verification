#!/usr/bin/env python3
"""Generate AI test label images into test-data/generated/.

Twenty fictitious products: 12 clean, 8 with planted compliance defects
(title-case warning, reworded warning, missing warning, missing net
contents, ABV that contradicts the catalog, missing bottler line, missing
class/type, import without a country-of-origin statement). The manifest
written alongside the images is ground truth for scoring model runs.

Usage:  OPENROUTER_API_KEY=... python scripts/generate_test_labels.py
"""

import base64
import json
import os
import sys
import time
from pathlib import Path

import httpx

OUT_DIR = Path("test-data/generated")
IMAGE_MODELS = ["google/gemini-3.1-flash-image-preview", "google/gemini-2.5-flash-image"]

WARNING_OK = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should "
    "not drink alcoholic beverages during pregnancy because of the risk of "
    "birth defects. (2) Consumption of alcoholic beverages impairs your "
    "ability to drive a car or operate machinery, and may cause health "
    "problems."
)
WARNING_TITLECASE = WARNING_OK.replace("GOVERNMENT WARNING:", "Government Warning:")
WARNING_REWORDED = WARNING_OK.replace(
    "Consumption of alcoholic beverages impairs", "Consumption of alcohol impairs"
)

# label_* = what gets printed on the image; catalog_* = the application of
# record. They differ only for the planted ABV-mismatch defect.
PRODUCTS = [
    # ------------------------------------------------------- 12 clean
    dict(pid="TTB-101", brand="Copper Canyon", cls="Straight Rye Whiskey",
         abv="45% Alc./Vol. (90 Proof)", net="750 mL",
         bottler="Distilled and Bottled by Copper Canyon Distilling Co., Boulder, CO",
         country="", warning="ok", defect="none"),
    dict(pid="TTB-102", brand="Silver Pine", cls="Vodka",
         abv="40% Alc./Vol.", net="750 mL",
         bottler="Distilled by Silver Pine Spirits, Boise, ID",
         country="", warning="ok", defect="none"),
    dict(pid="TTB-103", brand="Harbor Light", cls="London Dry Gin",
         abv="47% Alc./Vol. (94 Proof)", net="750 mL",
         bottler="Distilled and Bottled by Harbor Light Gin Works, Portland, ME",
         country="", warning="ok", defect="none"),
    dict(pid="TTB-104", brand="Casa Dorada", cls="Tequila Reposado 100% Agave",
         abv="40% Alc./Vol.", net="750 mL",
         bottler="Produced and Bottled by Destileria Casa Dorada, Jalisco, Mexico",
         country="Product of Mexico", warning="ok", defect="none"),
    dict(pid="TTB-105", brand="Blackwater Bay", cls="Caribbean Spiced Rum",
         abv="35% Alc./Vol.", net="750 mL",
         bottler="Bottled by Blackwater Bay Rum Co., Charleston, SC",
         country="", warning="ok", defect="none"),
    dict(pid="TTB-106", brand="Old Cooper's", cls="Kentucky Straight Bourbon Whiskey",
         abv="50% Alc./Vol. (100 Proof)", net="375 mL",
         bottler="Distilled and Bottled by Old Cooper's Distillery, Bardstown, KY",
         country="", warning="ok", defect="none"),
    dict(pid="TTB-107", brand="Glen Marrow", cls="Single Malt Scotch Whisky",
         abv="43% Alc./Vol.", net="700 mL",
         bottler="Distilled and Bottled in Scotland by Glen Marrow Distillers, Speyside, Scotland",
         country="Product of Scotland", warning="ok", defect="none"),
    dict(pid="TTB-108", brand="Frost Peak", cls="American Dry Gin",
         abv="44% Alc./Vol.", net="1 L",
         bottler="Distilled by Frost Peak Botanicals, Bozeman, MT",
         country="", warning="ok", defect="none"),
    dict(pid="TTB-109", brand="Rio Verde", cls="Mezcal Artesanal",
         abv="46% Alc./Vol.", net="750 mL",
         bottler="Produced by Mezcaleria Rio Verde, Oaxaca, Mexico",
         country="Product of Mexico", warning="ok", defect="none"),
    # Beer without ABV — legal under 27 CFR 7.65, must still PASS.
    dict(pid="TTB-110", brand="Golden Prairie", cls="Lager Beer",
         abv="", net="12 FL OZ",
         bottler="Brewed and Bottled by Golden Prairie Brewing Co., Omaha, NE",
         country="", warning="ok", defect="none",
         catalog_abv="4.8% Alc./Vol."),
    # Table wine without numeric ABV — legal under 27 CFR 4.36, must PASS.
    dict(pid="TTB-111", brand="Willow Creek", cls="Red Table Wine",
         abv="", net="750 mL",
         bottler="Produced and Bottled by Willow Creek Winery, Napa, CA",
         country="", warning="ok", defect="none",
         catalog_abv="12.5% Alc./Vol."),
    dict(pid="TTB-112", brand="North Fork", cls="Apple Brandy",
         abv="42% Alc./Vol. (84 Proof)", net="750 mL",
         bottler="Distilled by North Fork Orchards, Hudson, NY",
         country="", warning="ok", defect="none"),
    # ------------------------------------------------------- 8 defects
    dict(pid="TTB-113", brand="Iron Gate", cls="Vodka",
         abv="40% Alc./Vol.", net="750 mL",
         bottler="Distilled by Iron Gate Distillery, Detroit, MI",
         country="", warning="titlecase",
         defect="warning prefix in title case (must be ALL CAPS)"),
    dict(pid="TTB-114", brand="Cedar Hollow", cls="Tennessee Whiskey",
         abv="43% Alc./Vol.", net="750 mL",
         bottler="Distilled and Bottled by Cedar Hollow Distillery, Lynchburg, TN",
         country="", warning="reworded",
         defect='warning says "alcohol" instead of "alcoholic beverages"'),
    dict(pid="TTB-115", brand="Santa Lucia", cls="Tequila Blanco",
         abv="40% Alc./Vol.", net="750 mL",
         bottler="Produced and Bottled by Tequilera Santa Lucia, Jalisco, Mexico",
         country="Product of Mexico", warning="missing",
         defect="government warning entirely missing"),
    dict(pid="TTB-116", brand="Bay & Anchor", cls="Navy Strength Gin",
         abv="57% Alc./Vol. (114 Proof)", net="",
         bottler="Distilled by Bay & Anchor Spirits, Annapolis, MD",
         country="", warning="ok",
         defect="net contents missing", catalog_net="750 mL"),
    dict(pid="TTB-117", brand="Kings Hollow", cls="Straight Bourbon Whiskey",
         abv="40% Alc./Vol. (80 Proof)", net="750 mL",
         bottler="Distilled and Bottled by Kings Hollow Distilling, Frankfort, KY",
         country="", warning="ok",
         defect="label ABV 40% contradicts catalog 45% (catalog cross-check)",
         catalog_abv="45% Alc./Vol. (90 Proof)"),
    dict(pid="TTB-118", brand="White Falcon", cls="Vodka",
         abv="40% Alc./Vol.", net="750 mL",
         bottler="", country="", warning="ok",
         defect="bottler name-and-address statement missing",
         catalog_bottler="Distilled by White Falcon Spirits, Fargo, ND"),
    dict(pid="TTB-119", brand="Stone Mill", cls="",
         abv="45% Alc./Vol.", net="750 mL",
         bottler="Distilled by Stone Mill Distillery, Lancaster, PA",
         country="", warning="ok",
         defect="class/type designation missing",
         catalog_cls="Straight Bourbon Whiskey"),
    dict(pid="TTB-120", brand="Kiyomi", cls="Junmai Sake",
         abv="15% Alc./Vol.", net="720 mL",
         bottler="Brewed and Bottled by Kiyomi Shuzo, Kyoto, Japan",
         country="", warning="ok",
         defect="appears imported (Japanese brewer) but no country of origin statement"),
]

WARNING_TEXTS = {"ok": WARNING_OK, "titlecase": WARNING_TITLECASE, "reworded": WARNING_REWORDED}


def build_prompt(p: dict) -> str:
    lines = [f'Brand name (largest text): "{p["brand"]}"']
    if p["cls"]:
        lines.append(f'Class and type designation: "{p["cls"]}"')
    if p["abv"]:
        lines.append(f'Alcohol content: "{p["abv"]}"')
    if p["net"]:
        lines.append(f'Net contents: "{p["net"]}"')
    if p["bottler"]:
        lines.append(f'Bottler line (small text): "{p["bottler"]}"')
    if p["country"]:
        lines.append(f'Country of origin line: "{p["country"]}"')

    if p["warning"] == "missing":
        warning_part = (
            "Do NOT include any government warning text anywhere on the label."
        )
    else:
        emphasis = (
            'The opening phrase "Government Warning:" must be printed in title '
            "case exactly as written here — NOT in capital letters."
            if p["warning"] == "titlecase"
            else 'The opening phrase "GOVERNMENT WARNING:" must be in bold capital letters.'
        )
        warning_part = (
            "At the bottom, in a small but clearly legible text block, print this "
            f"statement EXACTLY character for character:\n{WARNING_TEXTS[p['warning']]}\n{emphasis}"
        )

    return (
        "Design a flat, front-facing rectangular alcohol beverage label on a "
        "subtle textured paper background, classic professional typography, "
        "high contrast, no bottle, no scene — just the label artwork filling "
        "the frame. Every piece of text below must be rendered EXACTLY as "
        "written, with perfect spelling, and remain crisply legible:\n\n"
        + "\n".join(lines)
        + "\n\n"
        + warning_part
        + "\n\nDo not add any other text, taglines, or numbers beyond what is specified."
    )


def generate(client: httpx.Client, key: str, prompt: str) -> bytes:
    last_error = None
    for model in IMAGE_MODELS:
        try:
            response = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "modalities": ["image", "text"],
                },
                timeout=120,
            )
            response.raise_for_status()
            message = response.json()["choices"][0]["message"]
            images = message.get("images") or []
            if not images:
                raise RuntimeError(f"{model} returned no image")
            data_url = images[0]["image_url"]["url"]
            return base64.b64decode(data_url.split(",", 1)[1])
        except Exception as exc:  # try the next model
            last_error = exc
    raise RuntimeError(f"all image models failed: {last_error}")


def main() -> int:
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        print("OPENROUTER_API_KEY is not set")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    manifest = []
    with httpx.Client() as client:
        for index, p in enumerate(PRODUCTS, 1):
            slug = p["brand"].lower().replace(" ", "-").replace("&", "and").replace("'", "")
            filename = f"{p['pid'].lower()}-{slug}.png"
            path = OUT_DIR / filename
            if not path.exists():
                print(f"[{index}/20] generating {filename} ...", flush=True)
                path.write_bytes(generate(client, key, build_prompt(p)))
                time.sleep(1)
            else:
                print(f"[{index}/20] {filename} exists, skipping")
            manifest.append({
                "filename": filename,
                "product_id": p["pid"],
                "brand": p["brand"],
                "defect": p["defect"],
                "expected_verdict": "pass" if p["defect"] == "none" else "fail",
                "label": {
                    "class_type": p["cls"], "alcohol_content": p["abv"],
                    "net_contents": p["net"], "bottler_address": p["bottler"],
                    "country_of_origin": p["country"], "warning": p["warning"],
                },
                "catalog": {
                    "brand_name": p["brand"],
                    "class_type": p.get("catalog_cls") or p["cls"],
                    "alcohol_content": p.get("catalog_abv") or p["abv"],
                    "net_contents": p.get("catalog_net") or p["net"],
                    "bottler_address": p.get("catalog_bottler") or p["bottler"],
                    "country_of_origin": p["country"],
                },
            })

    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {len(manifest)} entries to {OUT_DIR}/manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
