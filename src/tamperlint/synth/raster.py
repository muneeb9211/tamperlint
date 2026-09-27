"""Raster (scan / photo) versions of the synthetic documents, and pixel-level forgeries."""

from __future__ import annotations

import io

import cv2
import numpy as np
import pypdfium2 as pdfium


def render(pdf: bytes, dpi: int = 150, page: int = 0) -> np.ndarray:
    """Rasterise one PDF page to an 8-bit greyscale array (a clean "scan")."""
    doc = pdfium.PdfDocument(io.BytesIO(pdf))
    try:
        bitmap = doc[page].render(scale=dpi / 72, grayscale=True)
        image = np.array(bitmap.to_pil().convert("L"))
    finally:
        doc.close()
    return image


def scan(gray: np.ndarray, seed: int = 0, noise: float = 3.0) -> np.ndarray:
    """Make a clean render look like a scan: slight blur, paper tone and sensor noise."""
    rng = np.random.default_rng(seed)
    img = cv2.GaussianBlur(gray.astype(np.float32), (0, 0), 0.6) * 0.92 + 12
    img += rng.normal(0.0, noise, gray.shape)
    return np.asarray(np.clip(img, 0, 255), dtype=np.uint8)


def jpeg(gray: np.ndarray, quality: int = 80) -> bytes:
    ok, buf = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:  # pragma: no cover
        raise RuntimeError("JPEG encoding failed")
    return buf.tobytes()


def png(gray: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", gray)
    if not ok:  # pragma: no cover
        raise RuntimeError("PNG encoding failed")
    return buf.tobytes()


def decode(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:  # pragma: no cover
        raise ValueError("not a decodable image")
    return np.asarray(image)


def splice(
    host_jpeg: bytes,
    donor: np.ndarray,
    box: tuple[int, int, int, int],
    *,
    offset: tuple[int, int] = (3, 5),
    donor_quality: int = 70,
) -> np.ndarray:
    """Paste a region taken from a *separately compressed* donor, shifted off the host's grid.

    Returns the decoded result; save it with :func:`png` (lossless) or :func:`jpeg`.
    """
    host = decode(host_jpeg).copy()
    x0, y0, x1, y1 = box
    dx, dy = offset
    # Compress the donor on its own grid first, then shift it: the pasted pixels carry a JPEG
    # grid that is offset from the host's, as when content is cut from a different scan.
    donor_jpeg = decode(jpeg(donor, donor_quality))
    shifted = np.full_like(donor_jpeg, 255)
    shifted[dy:, dx:] = donor_jpeg[: donor_jpeg.shape[0] - dy, : donor_jpeg.shape[1] - dx]
    host[y0:y1, x0:x1] = shifted[y0:y1, x0:x1]
    return host


def copy_move(
    gray: np.ndarray, src: tuple[int, int, int, int], dest_xy: tuple[int, int]
) -> np.ndarray:
    """Copy a block of the image onto another location of the same image."""
    out = gray.copy()
    x0, y0, x1, y1 = src
    dx, dy = dest_xy
    out[dy : dy + (y1 - y0), dx : dx + (x1 - x0)] = gray[y0:y1, x0:x1]
    return out
