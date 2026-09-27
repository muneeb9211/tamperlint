"""Turn files into :class:`~tamperlint.document.Document` objects."""

from __future__ import annotations

from pathlib import Path

from tamperlint.document import Document
from tamperlint.loaders.image import IMAGE_SUFFIXES, load_image
from tamperlint.loaders.pdf import PdfLoadError, load_pdf

MAX_FILE_BYTES = 50 * 1024 * 1024


class UnsupportedFileError(ValueError):
    """Raised for files tamperlint cannot analyse."""


_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG", b"II*\x00", b"MM\x00*", b"RIFF")


def sniff_kind(data: bytes, name: str) -> str:
    # Image signatures are checked first: they sit at offset 0, while "%PDF-" may appear
    # anywhere in the first kilobyte (PDF allows leading junk), including inside image data.
    if data.startswith(_IMAGE_MAGIC):
        return "image"
    if b"%PDF-" in data[:1024]:
        return "pdf"
    if Path(name).suffix.lower() in IMAGE_SUFFIXES:
        return "image"
    raise UnsupportedFileError(f"{name}: not a PDF or a supported image format")


def load_bytes(data: bytes, name: str, max_bytes: int = MAX_FILE_BYTES) -> Document:
    if len(data) > max_bytes:
        raise UnsupportedFileError(
            f"{name}: file is {len(data) / 2**20:.1f} MB; the limit is {max_bytes / 2**20:.0f} MB"
        )
    kind = sniff_kind(data, name)
    try:
        return load_pdf(data, name) if kind == "pdf" else load_image(data, name)
    except PdfLoadError as exc:
        raise UnsupportedFileError(str(exc)) from exc
    except (OSError, ValueError) as exc:  # PIL: unreadable or oversized image
        raise UnsupportedFileError(f"{name}: the image could not be read ({exc})") from exc


def load_path(path: str | Path, max_bytes: int = MAX_FILE_BYTES) -> Document:
    p = Path(path)
    return load_bytes(p.read_bytes(), p.name, max_bytes)


__all__ = ["UnsupportedFileError", "load_bytes", "load_path", "sniff_kind"]
