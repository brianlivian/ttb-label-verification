#!/usr/bin/env python3
"""Produce degraded (grainy / blurry / angled / glared) variants of the
generated test labels, simulating the bad photography agents actually
receive. Sources keep their known content, so ground truth is unchanged —
the question each variant asks is whether the system still reads it, or
honestly flags uncertainty instead of guessing.

Usage:  python scripts/degrade_test_labels.py
"""

import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

OUT_DIR = Path("test-data/generated")


def add_noise(img: Image.Image, sigma: float) -> Image.Image:
    """Sensor grain: gaussian noise per channel."""
    pixels = np.asarray(img, dtype=np.float32)
    noisy = pixels + np.random.default_rng(7).normal(0, sigma, pixels.shape)
    return Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8))


def low_res_jpeg(img: Image.Image, width: int, quality: int) -> Image.Image:
    """Tiny sensor + aggressive compression."""
    small = img.resize((width, int(img.height * width / img.width)))
    buffer = io.BytesIO()
    small.save(buffer, format="JPEG", quality=quality)
    return Image.open(buffer).convert("RGB")


def motion_blur_dim(img: Image.Image) -> Image.Image:
    """Shaky hands in a dark stockroom."""
    blurred = img.filter(ImageFilter.GaussianBlur(2.2))
    return ImageEnhance.Brightness(ImageEnhance.Contrast(blurred).enhance(0.7)).enhance(0.6)


def perspective(img: Image.Image) -> Image.Image:
    """Photographed at an angle."""
    w, h = img.size
    squeeze = int(h * 0.16)
    return img.transform(
        (w, h),
        Image.Transform.QUAD,
        (0, squeeze, int(w * 0.06), h - squeeze, w, h, w, 0),
        fillcolor="#777",
    )


def glare(img: Image.Image) -> Image.Image:
    """Bright reflection across the middle of the label."""
    overlay = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(overlay)
    w, h = img.size
    draw.ellipse((int(w * 0.15), int(h * 0.30), int(w * 0.85), int(h * 0.62)), fill=215)
    overlay = overlay.filter(ImageFilter.GaussianBlur(40))
    return Image.composite(Image.new("RGB", img.size, "white"), img, overlay)


VARIANTS = [
    # (source file, suffix, transform, description)
    ("ttb-103-harbor-light.png", "grainy",
     lambda im: add_noise(im, 38), "heavy sensor grain"),
    ("ttb-105-blackwater-bay.png", "lowres",
     lambda im: low_res_jpeg(im, 380, 22), "low resolution + JPEG artifacts"),
    ("ttb-108-frost-peak.png", "blur-dim",
     motion_blur_dim, "motion blur, dim lighting"),
    ("ttb-113-iron-gate.png", "angled",
     lambda im: add_noise(perspective(im), 14), "photographed at an angle + grain"),
    ("ttb-106-old-coopers.png", "glare",
     glare, "glare across the middle of the label"),
    ("ttb-115-santa-lucia.png", "wrecked",
     lambda im: add_noise(low_res_jpeg(im, 240, 14), 30), "near-illegible: tiny, crushed, noisy"),
]


def main() -> int:
    manifest_path = OUT_DIR / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    by_name = {m["filename"]: m for m in manifest}

    for source, suffix, transform, description in VARIANTS:
        src_entry = by_name[source]
        out_name = source.replace(".png", f"-{suffix}.png")
        img = Image.open(OUT_DIR / source).convert("RGB")
        transform(img).save(OUT_DIR / out_name)
        print(f"wrote {out_name}  ({description})")

        if out_name not in by_name:
            entry = dict(src_entry)
            entry["filename"] = out_name
            entry["degraded"] = description
            # Same underlying label, so the same expected verdict — but for
            # degraded photos an honest NEEDS REVIEW / unreadable outcome is
            # acceptable (flagging uncertainty beats guessing).
            manifest.append(entry)
            by_name[out_name] = entry

    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nmanifest now has {len(manifest)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
