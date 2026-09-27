"""Load raster images (scans and photos) into a single-page :class:`Document`."""

from __future__ import annotations

import hashlib
import io

import cv2
import numpy as np
from PIL import Image, ImageOps

from tamperlint.document import Document, Page, Raster

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"}
MAX_PIXELS = 60_000_000


def load_image(data: bytes, name: str) -> Document:
    doc = Document(name=name, data=data, sha256=hashlib.sha256(data).hexdigest(), kind="image")
    with Image.open(io.BytesIO(data)) as img:
        width, height = img.size
        if width * height > MAX_PIXELS:
            raise ValueError(f"{name}: image is too large ({width}x{height})")
        doc.metadata.info["Format"] = str(img.format)
        is_jpeg = img.format == "JPEG"
    gray = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        with Image.open(io.BytesIO(data)) as img:
            gray = np.asarray(ImageOps.exif_transpose(img).convert("L"))
    # OpenCV applies the EXIF orientation, so take the page size from the pixels that are
    # actually analysed; finding coordinates and previews then line up.
    height, width = gray.shape[:2]
    doc.pages = [Page(number=1, width=float(width), height=float(height))]
    doc.page_count = 1
    doc.rasters = [Raster(page=1, gray=gray, bbox=None, source="file", jpeg=is_jpeg)]
    return doc
