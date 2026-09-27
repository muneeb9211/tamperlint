"""Load a PDF into a :class:`~tamperlint.document.Document`.

Three libraries are used, each for what it does best:

* **pikepdf** (qpdf) for low-level objects: revisions, trailer IDs, metadata, content streams.
* **pdfplumber** (pdfminer.six) for positioned characters, shapes, images and annotations.
* **pyHanko** for digital-signature integrity and post-signing modification analysis.

Nothing here executes PDF JavaScript or renders untrusted content with a full viewer.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pdfplumber
import pikepdf

from tamperlint.document import (
    Annotation,
    Char,
    Document,
    Metadata,
    Page,
    Raster,
    Revision,
    Shape,
    SignatureInfo,
    Word,
)
from tamperlint.models import BBox

log = logging.getLogger(__name__)

MAX_PAGES = 200  # pages whose structure (revisions, fonts, content streams) is read
MAX_TEXT_PAGES = 50  # pages whose text, layout and images are analysed: the costly part
MAX_PAGE_OPERATORS = 50_000  # drawing operations beyond which a page's layout is not read
MAX_REVISIONS = 100
_STARTXREF_RE = re.compile(rb"startxref\s+(\d+)\s*%%EOF[\r\n]*")
_PREV_RE = re.compile(rb"/Prev\s+(\d+)")
_XMP_AGENT_RE = re.compile(rb"softwareAgent(?:>|=\")([^<\"]{1,200})")


class PdfLoadError(ValueError):
    """The file is not a readable PDF (damaged, or encrypted with a password)."""


def load_pdf(data: bytes, name: str) -> Document:
    doc = Document(name=name, data=data, sha256=hashlib.sha256(data).hexdigest(), kind="pdf")
    try:
        pdf = pikepdf.open(io.BytesIO(data))
    except pikepdf.PasswordError as exc:
        raise PdfLoadError(f"{name}: the PDF is encrypted with a password") from exc
    except pikepdf.PdfError as exc:
        raise PdfLoadError(f"{name}: damaged or not a valid PDF") from exc
    with pdf:
        doc.is_linearized = b"/Linearized" in data[:2048]
        doc.page_count = len(pdf.pages)
        doc.metadata = _read_metadata(pdf)
        touchups, invisible, operators = _scan_content_streams(pdf, doc)
        opacity = _rect_fill_opacity(pdf)
        if doc.page_count > MAX_PAGES:
            doc.load_warnings.append(
                f"Revision and edit-marker checks covered the first {MAX_PAGES} of "
                f"{doc.page_count} pages."
            )
    doc.revisions = _read_revisions(data, doc)
    doc.signatures = _read_signatures(data, doc)
    doc.pages = _read_pages(data, doc, opacity, operators)
    doc.touchup_markers = touchups
    for page in doc.pages:
        page.touchup_markers = touchups.get(page.number, 0)
        page.invisible_chars = invisible.get(page.number, 0)
    return doc


# --------------------------------------------------------------------------- revisions


def _xref_chain_ends(data: bytes, doc: Document) -> list[int]:
    """End offsets of the incremental saves that belong to this document.

    Every save ends with ``startxref <offset> %%EOF``. Only saves whose cross-reference section
    is reachable from the final one through the ``/Prev`` chain are real revisions. Markers
    inside embedded files or comments, and the first-page marker of a linearised file (which
    points at offset 0), are ignored.
    """
    saves = [(m.end(), int(m.group(1))) for m in _STARTXREF_RE.finditer(data)]
    if not saves:
        return [len(data)]
    chain: set[int] = set()
    offset = saves[-1][1]
    while offset not in chain and 0 < offset < len(data) and len(chain) < 2 * MAX_REVISIONS:
        chain.add(offset)
        section = data[offset : offset + 262_144]
        stop = section.find(b"stream" if section[:4] != b"xref" else b"startxref")
        m = _PREV_RE.search(section[: stop if stop > 0 else len(section)])
        if not m:
            break
        offset = int(m.group(1))
    ends = [end for end, xref in saves if xref in chain]
    ignored = len(saves) - len(ends)
    if ignored and not (doc.is_linearized and ignored == 1):
        doc.load_warnings.append(
            f"{ignored} end-of-file marker(s) were ignored because they are not part of the "
            "document's cross-reference chain (for example, inside an attachment)."
        )
    if not ends:
        return [saves[-1][0]]
    if len(ends) > MAX_REVISIONS:
        doc.load_warnings.append(f"Only the last {MAX_REVISIONS} revisions were analysed.")
        ends = ends[-MAX_REVISIONS:]
    return ends


def _read_revisions(data: bytes, doc: Document) -> list[Revision]:
    revisions: list[Revision] = []
    for idx, end in enumerate(_xref_chain_ends(data, doc), start=1):
        rev = Revision(index=idx, end_offset=end, readable=False)
        try:
            with pikepdf.open(io.BytesIO(data[:end])) as pdf:
                info: Any = pdf.docinfo if "/Info" in pdf.trailer else {}
                rev.producer = _as_text(info.get("/Producer")) if info else None
                rev.creator = _as_text(info.get("/Creator")) if info else None
                ids = pdf.trailer.get("/ID")
                if isinstance(ids, pikepdf.Array) and len(ids) == 2:
                    rev.permanent_id = bytes(ids[0]).hex()
                    rev.changing_id = bytes(ids[1]).hex()
                rev.page_count = len(pdf.pages)
                for page in list(pdf.pages)[:MAX_PAGES]:
                    rev.page_content_hashes.append(
                        hashlib.sha256(_content_bytes(page.obj)).hexdigest()
                    )
                    rev.page_fonts.append(frozenset(_page_font_names(page.obj)))
            rev.readable = True  # only once every page was read
        except Exception as exc:
            log.debug("revision %d unreadable: %s", idx, exc)
            rev.page_content_hashes, rev.page_fonts = [], []
            doc.load_warnings.append(f"Revision {idx} could not be parsed on its own.")
        revisions.append(rev)
    return revisions


def _content_bytes(page_obj: Any) -> bytes:
    contents = page_obj.get("/Contents")
    if contents is None:
        return b""
    try:
        if isinstance(contents, pikepdf.Array):
            return b"\n".join(s.read_bytes() for s in contents)
        return bytes(contents.read_bytes())
    except Exception:
        return b""


def _inherited(page_obj: Any, key: str) -> Any:
    node = page_obj
    for _ in range(32):
        if node is None:
            return None
        value = node.get(key)
        if value is not None:
            return value
        node = node.get("/Parent")
    return None


def _page_font_names(page_obj: Any) -> Iterator[str]:
    """Base-font names of a page's font resources; malformed entries are skipped."""
    resources = _inherited(page_obj, "/Resources")
    if not isinstance(resources, pikepdf.Dictionary):
        return
    fonts = resources.get("/Font")
    if not isinstance(fonts, pikepdf.Dictionary):
        return
    for _key, font in fonts.items():
        if not isinstance(font, pikepdf.Dictionary):
            continue
        base = font.get("/BaseFont")
        if base is not None:
            yield str(base).lstrip("/")


# --------------------------------------------------------------------------- metadata


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return str(value).strip() or None
    except Exception:
        return None


def _read_metadata(pdf: pikepdf.Pdf) -> Metadata:
    meta = Metadata()
    try:
        for key, value in pdf.docinfo.items():
            text = _as_text(value)
            if text:
                meta.info[str(key).lstrip("/")] = text
    except Exception:
        pass
    try:
        # read only: do not sync the XMP back into the document information dictionary
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as xmp:
            for key in ("xmp:CreateDate", "xmp:ModifyDate", "xmp:CreatorTool", "pdf:Producer"):
                value = xmp.get(key)
                if value:
                    meta.xmp[key] = str(value)
    except Exception:
        pass
    try:
        raw = pdf.Root.Metadata.read_bytes() if "/Metadata" in pdf.Root else b""
        meta.xmp_history_agents = [
            m.decode("utf-8", "replace").strip() for m in _XMP_AGENT_RE.findall(raw)
        ]
    except Exception:
        pass
    return meta


# --------------------------------------------------------------------------- content streams


def _form_operators(resources: Any, seen: set[tuple[int, int]], depth: int = 0) -> int:
    """Drawing operators inside the Form XObjects a page (or form) uses."""
    total = 0
    try:
        xobjects = resources.get("/XObject") if resources is not None else None
        forms = [x for _, x in xobjects.items()] if xobjects is not None else []
    except Exception:
        return 0
    for xo in forms:
        try:
            if xo.get("/Subtype") != "/Form" or xo.objgen in seen or depth > 3:
                continue
            seen.add(xo.objgen)
            total += len(pikepdf.parse_content_stream(xo))
            total += _form_operators(xo.get("/Resources"), seen, depth + 1)
        except Exception:
            continue
    return total


def _scan_content_streams(
    pdf: pikepdf.Pdf, doc: Document
) -> tuple[dict[int, int], dict[int, int], dict[int, int]]:
    """Per page: Acrobat TouchUp markers, text runs drawn in invisible render mode, and the
    number of drawing operators (including forms), which predicts the cost of layout analysis."""
    touchups: dict[int, int] = {}
    invisible: dict[int, int] = {}
    operators: dict[int, int] = {}
    for number, page in enumerate(list(pdf.pages)[:MAX_PAGES], start=1):
        try:
            ops = pikepdf.parse_content_stream(page)
        except Exception:
            doc.load_warnings.append(f"Content stream of page {number} could not be parsed.")
            continue
        operators[number] = len(ops) + _form_operators(page.obj.get("/Resources"), set())
        mode_stack = [0]
        t_count = i_count = 0
        for instruction in ops:
            operands, op = instruction.operands, str(instruction.operator)
            if op == "q":
                mode_stack.append(mode_stack[-1])
            elif op == "Q" and len(mode_stack) > 1:
                mode_stack.pop()
            elif op == "Tr" and operands:
                try:
                    mode_stack[-1] = int(operands[0])
                except (TypeError, ValueError):
                    continue  # malformed operand; a viewer would ignore it too
            elif (
                op in ("BDC", "BMC", "MP", "DP")
                and operands
                and str(operands[0]) == "/TouchUp_TextEdit"
            ):
                t_count += 1
            elif op in ("Tj", "TJ", "'", '"') and mode_stack[-1] == 3:
                i_count += 1
        if t_count:
            touchups[number] = t_count
        if i_count:
            invisible[number] = i_count
    return touchups, invisible, operators


_FILL_OPS = {"f", "F", "f*", "B", "B*", "b", "b*"}
_PAINT_OPS = _FILL_OPS | {"S", "s"}


def _rect_fill_opacity(pdf: pikepdf.Pdf) -> dict[int, list[float]]:
    """Per page, the fill opacity of every painted rectangle, in drawing order.

    pdfplumber reports rectangles but not their transparency or blend mode. A semi-transparent
    highlight or a multiply-blended zebra stripe drawn after text does not hide that text, so
    the hidden-text check needs this. Opacity is 0 for rectangles that are only stroked, and
    for fills in a non-Normal blend mode. The list follows the same order as pdfminer's
    rectangles; callers must discard it if the counts differ.
    """
    result: dict[int, list[float]] = {}
    for number, page in enumerate(list(pdf.pages)[:MAX_PAGES], start=1):
        out: list[float] = []
        try:
            _walk_rects(page.obj, _inherited(page.obj, "/Resources"), [1.0, True], out, 0)
        except Exception:
            continue
        result[number] = out
    return result


def _walk_rects(obj: Any, resources: Any, state: list[Any], out: list[float], depth: int) -> None:
    if depth > 8:
        return
    stack: list[list[Any]] = []
    rects_in_path = 0
    for instruction in pikepdf.parse_content_stream(obj):
        op = str(instruction.operator)
        operands = instruction.operands
        if op == "q":
            stack.append(list(state))
        elif op == "Q" and stack:
            state[:] = stack.pop()
        elif op == "gs" and operands and resources is not None:
            gs = (resources.get("/ExtGState") or {}).get(str(operands[0]))
            if isinstance(gs, pikepdf.Dictionary):
                if "/ca" in gs:
                    state[0] = float(gs["/ca"])
                if "/BM" in gs:
                    mode = gs["/BM"]
                    mode = mode[0] if isinstance(mode, pikepdf.Array) and len(mode) else mode
                    state[1] = str(mode) in ("/Normal", "/Compatible")
        elif op == "re":
            rects_in_path += 1
        elif op in _PAINT_OPS:
            opacity = state[0] if (op in _FILL_OPS and state[1]) else 0.0
            out.extend([opacity] * rects_in_path)
            rects_in_path = 0
        elif op == "n":
            rects_in_path = 0  # clipping path, not painted
        elif op == "Do" and operands and resources is not None:
            xobj = (resources.get("/XObject") or {}).get(str(operands[0]))
            if isinstance(xobj, pikepdf.Stream) and xobj.get("/Subtype") == "/Form":
                inner = xobj.get("/Resources", resources)
                _walk_rects(xobj, inner, list(state), out, depth + 1)


# --------------------------------------------------------------------------- signatures


def _read_signatures(data: bytes, doc: Document) -> list[SignatureInfo]:
    if b"/ByteRange" not in data:
        return []
    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
    except ImportError:  # pragma: no cover - pyHanko is a core dependency
        doc.load_warnings.append("pyHanko is not installed; signatures were not analysed.")
        return []
    # pyHanko's validator drives its own event loop with asyncio.run(), which fails inside a
    # running loop (Jupyter, async web handlers). Run it on a worker thread in that case.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return _validate_signatures(data, doc, PdfFileReader, validate_pdf_signature)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(
            _validate_signatures, data, doc, PdfFileReader, validate_pdf_signature
        ).result()


def _validate_signatures(
    data: bytes, doc: Document, reader_cls: Any, validate: Any
) -> list[SignatureInfo]:
    from pyhanko.sign.fields import enumerate_sig_fields
    from pyhanko.sign.validation.pdf_embedded import EmbeddedPdfSignature
    from pyhanko_certvalidator import ValidationContext

    try:
        reader = reader_cls(io.BytesIO(data), strict=False)
        if reader.encrypted:
            reader.decrypt("")  # files that open without a password use an empty user password
        fields = list(enumerate_sig_fields(reader, filled_status=True))
    except Exception as exc:
        log.debug("signature fields unreadable: %s", exc)
        return [_broken_signature("signature", "the signature fields could not be read")]
    results: list[SignatureInfo] = []
    for name, _value, field_ref in fields:
        field_name = str(name)
        try:
            sig = EmbeddedPdfSignature(reader, field_ref.get_object(), field_name)
            # Integrity only. The signer's own certificate is the sole trust root, so path
            # building trivially succeeds and nothing is fetched from the network: tamperlint
            # checks whether the signed bytes and the changes after signing are consistent, not
            # who the signer is (the operating system's trust store would be non-deterministic).
            context = ValidationContext(trust_roots=[sig.signer_cert], allow_fetching=False)
            status = validate(sig, signer_validation_context=context)
        except Exception as exc:
            log.debug("signature %s could not be validated: %s", field_name, exc)
            results.append(_broken_signature(field_name, "it could not be parsed or validated"))
            continue
        info = SignatureInfo(
            field_name=field_name,
            intact=bool(status.intact),
            valid=bool(status.valid),
            coverage=getattr(status.coverage, "name", None),
            modification_level=getattr(status.modification_level, "name", None),
            docmdp_ok=status.docmdp_ok,
            certification=sig.docmdp_level is not None,
        )
        byte_range = getattr(sig, "byte_range", None)
        if byte_range:
            info.signed_revision_end = int(byte_range[2]) + int(byte_range[3])
        results.append(info)
    return results


def _broken_signature(field_name: str, reason: str) -> SignatureInfo:
    return SignatureInfo(
        field_name=field_name,
        intact=None,
        valid=False,
        coverage=None,
        modification_level=None,
        docmdp_ok=None,
        error=reason,
    )


# --------------------------------------------------------------------------- pages


def _color(value: Any) -> tuple[float, ...] | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return (float(value),)
    try:
        return tuple(float(v) for v in value)
    except (TypeError, ValueError):
        return None


def _drawing_order(layout: Any) -> tuple[list[int], list[int], list[int]]:
    """Global drawing-order index of every character, rectangle and curve on a page.

    pdfplumber keeps characters, rectangles and curves in separate lists, each in
    content-stream order, but loses the order *between* them. Walking pdfminer's layout tree
    recovers it, so we can tell a box drawn over text from a table background drawn underneath.
    """
    from pdfminer.layout import LTChar, LTContainer, LTCurve, LTImage, LTLine, LTRect

    seq = 0
    chars: list[int] = []
    rects: list[int] = []
    curves: list[int] = []

    def walk(objs: Any) -> None:
        nonlocal seq
        for obj in objs:
            if isinstance(obj, LTChar):
                chars.append(seq)
            elif isinstance(obj, LTRect):
                rects.append(seq)
            elif isinstance(obj, LTCurve) and not isinstance(obj, LTLine):
                curves.append(seq)
            seq += 1 if isinstance(obj, (LTChar, LTCurve, LTImage)) else 0
            if isinstance(obj, LTContainer) and not isinstance(obj, LTChar):
                walk(obj)

    walk(layout)
    return chars, rects, curves


def _white_box(curve: dict[str, Any]) -> bool:
    """A filled path that is an axis-aligned rectangle painted white: a whiteout.

    Editors often draw correction boxes as paths rather than rectangles. Coloured paths are
    left out: their transparency is unknown, and highlighter marks are drawn this way.
    """
    if not curve.get("fill"):
        return False
    corners = {(round(float(p[0]), 1), round(float(p[1]), 1)) for p in curve.get("pts") or []}
    if len(corners) != 4:  # closing points repeat the first corner
        return False
    xs, ys = [x for x, _ in corners], [y for _, y in corners]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    if x1 - x0 < 1 or y1 - y0 < 1:
        return False
    if not all(min(abs(x - x0), abs(x - x1)) < 0.5 for x in xs):
        return False
    if not all(min(abs(y - y0), abs(y - y1)) < 0.5 for y in ys):
        return False
    color = curve.get("non_stroking_color")
    values = color if isinstance(color, (list, tuple)) else (color,)
    try:
        numbers = [float(v) for v in values]
    except (TypeError, ValueError):
        return False
    if len(numbers) == 4:  # CMYK: white is no ink
        return all(v <= 0.05 for v in numbers)
    return bool(numbers) and all(v >= 0.95 for v in numbers)


def _mark_hidden(page: Page) -> None:
    """Flag characters hidden under a later filled shape or overprinted by later, different text."""
    from tamperlint.util import overlap_ratio

    # Only opaque, normally blended fills hide what is underneath; highlights and tinted
    # table stripes are drawn translucent or with a multiply blend.
    covers = [
        s for s in page.shapes
        if s.filled and s.order >= 0 and (s.opacity is None or s.opacity >= OPAQUE)
    ]  # fmt: skip
    # Grid indexes keep this linear on pages with thousands of characters and shapes.
    cell = 48.0
    cover_cells: dict[tuple[int, int], list[Shape]] = {}
    for shape in covers:
        for gx in range(int(shape.x0 // cell), int(shape.x1 // cell) + 1):
            for gy in range(int(shape.top // cell), int(shape.bottom // cell) + 1):
                cover_cells.setdefault((gx, gy), []).append(shape)
    buckets: dict[tuple[int, int], list[Char]] = {}
    for ch in page.chars:
        buckets.setdefault((int(ch.x0 // 24), int(ch.top // 24)), []).append(ch)
    for ch in page.chars:
        box = (ch.x0, ch.top, ch.x1, ch.bottom)
        if ch.order >= 0 and cover_cells:
            near = {
                id(s): s
                for gx in range(int(ch.x0 // cell), int(ch.x1 // cell) + 1)
                for gy in range(int(ch.top // cell), int(ch.bottom // cell) + 1)
                for s in cover_cells.get((gx, gy), ())
            }
            for shape in near.values():
                if (
                    shape.order > ch.order
                    and overlap_ratio(box, (shape.x0, shape.top, shape.x1, shape.bottom)) >= 0.6
                ):
                    ch.hidden, ch.hidden_by = True, "shape"
                    ch.cover = (shape.x0, shape.top, shape.x1, shape.bottom)
                    break
        if ch.hidden or not ch.text.strip() or not ch.upright:
            continue  # rotated text (e.g. diagonal watermarks) is not overprint evidence
        bx, by = int(ch.x0 // 24), int(ch.top // 24)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in buckets.get((bx + dx, by + dy), ()):
                    if (
                        other.order > ch.order
                        and other.text != ch.text
                        and other.text.strip()
                        and other.upright
                        and 0.7 <= other.size / max(ch.size, 0.1) <= 1.4  # ignore watermarks
                        and overlap_ratio(box, (other.x0, other.top, other.x1, other.bottom)) >= 0.5
                        # the same spot, not a neighbour squeezed close by tight tracking
                        and abs((other.x0 + other.x1) - (ch.x0 + ch.x1)) / 2
                        <= 0.35 * max(other.x1 - other.x0, ch.x1 - ch.x0)
                    ):
                        ch.hidden, ch.hidden_by = True, "text"
                        break
                if ch.hidden:
                    break
            if ch.hidden:
                break


OPAQUE = 0.95
MIN_RASTER_SIDE = 256
MAX_RASTER_PIXELS = 30_000_000


SCAN_PAGE_SHARE = 0.2  # an embedded image this large (of the page) may be a scanned document
PAPER_SHARE = 0.5  # share of light pixels that makes an image look like paper, not a photo
PAPER_LEVEL = 190


def _embedded_jpeg(img: dict[str, Any], page_number: int, box: BBox) -> Raster | None:
    """Decode a JPEG (DCTDecode) image embedded in the page, keeping its compression traces.

    Only images of paper (a scanned page, receipt or letter) are kept for pixel forensics.
    Photographs and artwork in designed documents are routinely composited, so their JPEG
    traces say nothing about the document.
    """
    import cv2
    import numpy as np

    stream = img.get("stream")
    if stream is None:
        return None
    try:
        names = [str(getattr(name, "name", name)).lstrip("/") for name, _ in stream.get_filters()]
    except Exception:
        return None
    if not names or names[-1] != "DCTDecode":
        return None
    try:
        raw = stream.get_rawdata()
        w, h = int(img.get("srcsize", (0, 0))[0]), int(img.get("srcsize", (0, 0))[1])
        if min(w, h) < MIN_RASTER_SIDE or w * h > MAX_RASTER_PIXELS:
            return None
        gray = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    except Exception:
        return None
    if gray is None or (gray >= PAPER_LEVEL).mean() < PAPER_SHARE:
        return None
    return Raster(page=page_number, gray=gray, bbox=box, source="embedded", jpeg=True)


def _axis_aligned(matrix: Any) -> bool:
    """False for text drawn at an angle, such as diagonal watermarks."""
    if not matrix:
        return True
    return abs(float(matrix[1])) < 1e-3 and abs(float(matrix[2])) < 1e-3


def _char_key(x0: float, top: float, text: str) -> tuple[float, float, str]:
    return (round(x0, 1), round(top, 1), text)


def _double_struck(chars: list[Char]) -> bool:
    """True when a glyph is drawn twice within a point, as some generators do for a bolder
    look. A linear check, so the slower de-duplication only runs on pages that need it."""
    seen: dict[tuple[str, str, int, int], list[Char]] = {}
    for ch in chars:
        if not ch.text.strip():
            continue
        gx, gy = int(ch.x0), int(ch.top)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other in seen.get((ch.text, ch.fontname, gx + dx, gy + dy), ()):
                    if abs(other.x0 - ch.x0) <= 1 and abs(other.top - ch.top) <= 1:
                        return True
        seen.setdefault((ch.text, ch.fontname, gx, gy), []).append(ch)
    return False


def _not_hidden(hidden: set[tuple[float, float, str]]) -> Any:
    """pdfplumber filter that keeps visible, horizontal text, so words (and every text-based
    check built on them) ignore hidden characters and angled watermarks."""

    def keep(obj: dict[str, Any]) -> bool:
        if obj.get("object_type") != "char":
            return True
        if not _axis_aligned(obj.get("matrix")):
            return False
        return _char_key(float(obj["x0"]), float(obj["top"]), obj.get("text", "")) not in hidden

    return keep


def _read_pages(
    data: bytes, doc: Document, opacity: dict[int, list[float]], operators: dict[int, int]
) -> list[Page]:
    pages: list[Page] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for number, p in enumerate(pdf.pages[:MAX_TEXT_PAGES], start=1):
            page = Page(number=number, width=float(p.width), height=float(p.height))
            if operators.get(number, 0) > MAX_PAGE_OPERATORS:
                # Maps and technical drawings hold hundreds of thousands of drawing operations,
                # which take minutes to lay out; financial documents are far simpler.
                doc.load_warnings.append(
                    f"Page {number} is a complex drawing ({operators[number]:,} drawing "
                    "operations); its text and layout were not analysed."
                )
                pages.append(page)
                continue
            try:
                raw_chars, raw_rects, raw_curves = p.chars, p.rects, p.curves
                char_order, rect_order, curve_order = _drawing_order(p.layout)
                if len(char_order) != len(raw_chars) or len(rect_order) != len(raw_rects):
                    char_order, rect_order = [-1] * len(raw_chars), [-1] * len(raw_rects)
                    doc.load_warnings.append(
                        f"Drawing order of page {number} could not be recovered."
                    )
                if len(curve_order) != len(raw_curves) or rect_order[:1] == [-1]:
                    curve_order = [-1] * len(raw_curves)
                for c, order in zip(raw_chars, char_order, strict=True):
                    page.chars.append(
                        Char(
                            text=c.get("text", ""),
                            fontname=str(c.get("fontname", "")),
                            size=float(c.get("size", 0.0)),
                            x0=float(c["x0"]),
                            x1=float(c["x1"]),
                            top=float(c["top"]),
                            bottom=float(c["bottom"]),
                            upright=bool(c.get("upright", True)) and _axis_aligned(c.get("matrix")),
                            order=order,
                            baseline=float(p.height)
                            - float((c.get("matrix") or (0, 0, 0, 0, 0, 0))[5]),
                        )
                    )
                known = opacity.get(number)
                fills: list[float | None] = (
                    list(known)
                    if known is not None and len(known) == len(raw_rects)
                    else [None] * len(raw_rects)  # unknown: treat fills as opaque
                )
                for r, order, fill in zip(raw_rects, rect_order, fills, strict=True):
                    page.shapes.append(
                        Shape(
                            x0=float(r["x0"]),
                            x1=float(r["x1"]),
                            top=float(r["top"]),
                            bottom=float(r["bottom"]),
                            filled=bool(r.get("fill")),
                            color=_color(r.get("non_stroking_color")),
                            order=order,
                            opacity=fill,
                        )
                    )
                for curve, order in zip(raw_curves, curve_order, strict=True):
                    if order >= 0 and _white_box(curve):
                        page.shapes.append(
                            Shape(
                                x0=float(curve["x0"]),
                                x1=float(curve["x1"]),
                                top=float(curve["top"]),
                                bottom=float(curve["bottom"]),
                                filled=True,
                                color=_color(curve.get("non_stroking_color")),
                                order=order,
                                opacity=1.0,
                            )
                        )
                _mark_hidden(page)
                hidden = {_char_key(c.x0, c.top, c.text) for c in page.chars if c.hidden}
                visible = p.filter(_not_hidden(hidden))
                if _double_struck(page.chars):  # words read double-struck text once
                    visible = visible.dedupe_chars(tolerance=1)
                for w in visible.extract_words(
                    extra_attrs=["fontname", "size"], use_text_flow=False
                ):
                    page.words.append(
                        Word(
                            text=w["text"],
                            x0=float(w["x0"]),
                            x1=float(w["x1"]),
                            top=float(w["top"]),
                            bottom=float(w["bottom"]),
                            fontname=str(w.get("fontname", "")),
                            size=float(w.get("size", 0.0)),
                        )
                    )
                for img in p.images:
                    box = BBox(
                        x0=float(img["x0"]),
                        top=float(img["top"]),
                        x1=float(img["x1"]),
                        bottom=float(img["bottom"]),
                    )
                    page.image_boxes.append(box)
                    if box.width * box.height < SCAN_PAGE_SHARE * page.width * page.height:
                        continue
                    raster = _embedded_jpeg(img, number, box)
                    if raster is not None:
                        doc.rasters.append(raster)
                for a in p.annots:
                    subtype = (a.get("data") or {}).get("Subtype")
                    page.annotations.append(
                        Annotation(
                            subtype=str(getattr(subtype, "name", subtype or "")).lstrip("/"),
                            x0=float(a["x0"]),
                            x1=float(a["x1"]),
                            top=float(a["top"]),
                            bottom=float(a["bottom"]),
                            contents=a.get("contents"),
                        )
                    )
            except Exception as exc:
                doc.load_warnings.append(f"Page {number} layout could not be fully read: {exc}")
            pages.append(page)
        if len(pdf.pages) > MAX_TEXT_PAGES:
            doc.load_warnings.append(
                f"Text, layout and image checks covered the first {MAX_TEXT_PAGES} of "
                f"{len(pdf.pages)} pages."
            )
    return pages
