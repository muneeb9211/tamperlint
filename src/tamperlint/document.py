"""Internal representation of a loaded document, shared by all detectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from tamperlint.models import BBox


@dataclass(slots=True)
class Char:
    text: str
    fontname: str
    size: float
    x0: float
    x1: float
    top: float
    bottom: float
    upright: bool = True
    order: int = -1  # position in the page's drawing order
    baseline: float = 0.0  # text baseline, distance from the top of the page
    hidden: bool = False  # covered by a later filled shape or by later, different text
    hidden_by: str | None = None  # "shape" or "text"
    cover: tuple[float, float, float, float] | None = None  # the shape that hides it

    @property
    def bbox(self) -> BBox:
        return BBox(x0=self.x0, top=self.top, x1=self.x1, bottom=self.bottom)

    @property
    def base_font(self) -> str:
        """Font name without the six-letter subset prefix (ABCDEF+Helvetica -> Helvetica)."""
        return strip_subset_prefix(self.fontname)


@dataclass(slots=True)
class Word:
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    fontname: str
    size: float

    @property
    def bbox(self) -> BBox:
        return BBox(x0=self.x0, top=self.top, x1=self.x1, bottom=self.bottom)


@dataclass(slots=True)
class Shape:
    x0: float
    x1: float
    top: float
    bottom: float
    filled: bool
    color: tuple[float, ...] | None
    order: int = -1
    opacity: float | None = None  # fill opacity; 0 for strokes and non-Normal blends

    @property
    def bbox(self) -> BBox:
        return BBox(x0=self.x0, top=self.top, x1=self.x1, bottom=self.bottom)


@dataclass(slots=True)
class Annotation:
    subtype: str
    x0: float
    x1: float
    top: float
    bottom: float
    contents: str | None = None

    @property
    def bbox(self) -> BBox:
        return BBox(x0=self.x0, top=self.top, x1=self.x1, bottom=self.bottom)


@dataclass(slots=True)
class Page:
    number: int  # 1-based
    width: float
    height: float
    chars: list[Char] = field(default_factory=list)
    words: list[Word] = field(default_factory=list)
    shapes: list[Shape] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    image_boxes: list[BBox] = field(default_factory=list)
    invisible_chars: int = 0
    touchup_markers: int = 0

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    @property
    def has_full_page_image(self) -> bool:
        page_area = self.width * self.height
        return any(b.width * b.height >= 0.6 * page_area for b in self.image_boxes)


@dataclass(slots=True)
class Revision:
    """State of the document at the end of one incremental save (1-based index)."""

    index: int
    end_offset: int
    readable: bool
    page_count: int = 0
    producer: str | None = None
    creator: str | None = None
    permanent_id: str | None = None
    changing_id: str | None = None
    page_content_hashes: list[str] = field(default_factory=list)
    page_fonts: list[frozenset[str]] = field(default_factory=list)


@dataclass(slots=True)
class SignatureInfo:
    field_name: str
    intact: bool | None
    valid: bool | None
    coverage: str | None
    modification_level: str | None
    docmdp_ok: bool | None
    certification: bool = False  # a certification (DocMDP) signature, not an approval one
    signed_revision_end: int | None = None
    error: str | None = None


@dataclass(slots=True)
class Metadata:
    info: dict[str, str] = field(default_factory=dict)
    xmp: dict[str, str] = field(default_factory=dict)
    xmp_history_agents: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Raster:
    """Pixels to analyse: an input image, or a JPEG embedded in a PDF page."""

    page: int
    gray: Any  # numpy.ndarray, uint8, shape (h, w)
    bbox: BBox | None  # position on the PDF page; None for image inputs (pixel coordinates)
    source: Literal["file", "embedded"]
    jpeg: bool

    def to_page(self, x0: float, y0: float, x1: float, y1: float) -> BBox:
        """Map a pixel rectangle to page coordinates (identity for image inputs)."""
        if self.bbox is None:
            return BBox(x0=x0, top=y0, x1=x1, bottom=y1)
        h, w = self.gray.shape[:2]
        sx, sy = self.bbox.width / w, self.bbox.height / h
        return BBox(
            x0=self.bbox.x0 + x0 * sx,
            top=self.bbox.top + y0 * sy,
            x1=self.bbox.x0 + x1 * sx,
            bottom=self.bbox.top + y1 * sy,
        )


@dataclass(slots=True)
class Document:
    name: str
    data: bytes
    sha256: str
    kind: Literal["pdf", "image"]
    pages: list[Page] = field(default_factory=list)
    revisions: list[Revision] = field(default_factory=list)
    signatures: list[SignatureInfo] = field(default_factory=list)
    metadata: Metadata = field(default_factory=Metadata)
    is_linearized: bool = False
    page_count: int = 0  # total pages, even beyond the analysis limit
    doc_type: str = "generic"
    load_warnings: list[str] = field(default_factory=list)
    rasters: list[Raster] = field(default_factory=list)
    # Acrobat TouchUp markers per page number, for every page whose content was scanned
    # (beyond the pages whose text is analysed)
    touchup_markers: dict[int, int] = field(default_factory=dict)

    @property
    def is_signed(self) -> bool:
        return bool(self.signatures)

    @property
    def text(self) -> str:
        return "\n".join(p.text for p in self.pages)


def strip_subset_prefix(fontname: str) -> str:
    if (
        len(fontname) > 7
        and fontname[6] == "+"
        and fontname[:6].isalpha()
        and fontname[:6].isupper()
    ):
        return fontname[7:]
    return fontname


def subset_prefix(fontname: str) -> str | None:
    base = strip_subset_prefix(fontname)
    return None if base == fontname else fontname[:6]
