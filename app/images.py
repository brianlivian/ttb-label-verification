"""Image validation and preparation for vision calls.

Uploads up to 10MB are accepted, but oversized images cost both latency and
tokens on the vision API. Everything is decoded with Pillow (which doubles
as a corrupt-file check) and re-encoded as a JPEG no larger than MAX_EDGE
pixels on its long side.
"""

import io

from PIL import Image, UnidentifiedImageError

# Plenty for reading label text, small enough to keep vision calls fast.
MAX_EDGE = 1600
JPEG_QUALITY = 85


class UnreadableImageError(Exception):
    """Raised when an upload cannot be decoded as an image."""


def prepare_image(data: bytes) -> tuple[bytes, str]:
    """Return (jpeg_bytes, media_type) ready to send to the vision model."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise UnreadableImageError(
            "The file could not be read as an image. It may be corrupt or not "
            "actually an image file."
        ) from exc

    if img.mode != "RGB":
        img = img.convert("RGB")

    if max(img.size) > MAX_EDGE:
        img.thumbnail((MAX_EDGE, MAX_EDGE))

    out = io.BytesIO()
    img.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue(), "image/jpeg"
