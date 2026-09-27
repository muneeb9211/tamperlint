"""HTTP API (FastAPI).

    pip install "tamperlint[server] @ git+https://github.com/muneeb9211/tamperlint"
    tamperlint serve --port 8000

Uploads are analysed in memory and are never kept. Note that the web framework spools request
bodies larger than 1 MB to a temporary file for the duration of the request; put the service
behind a reverse proxy with its own body-size limit in production.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Annotated, Any, Literal

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

from tamperlint import __version__
from tamperlint.api import check
from tamperlint.loaders import UnsupportedFileError
from tamperlint.models import Report
from tamperlint.report.html import render_html
from tamperlint.report.sarif import to_sarif
from tamperlint.rules import RULES
from tamperlint.util import quiet_parser_logs

log = logging.getLogger("tamperlint.server")

MAX_UPLOAD_BYTES = int(os.environ.get("TAMPERLINT_MAX_UPLOAD_MB", "20")) * 1024 * 1024
TIMEOUT_SECONDS = float(os.environ.get("TAMPERLINT_TIMEOUT_SECONDS", "60"))
MAX_CONCURRENCY = int(os.environ.get("TAMPERLINT_MAX_CONCURRENCY", "2"))
# The report embeds images as data: URIs and inline styles, and never runs scripts.
_HTML_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}

quiet_parser_logs()

app = FastAPI(
    title="tamperlint",
    version=__version__,
    description="Explainable tamper detection for PDFs and scanned documents.",
)


class _Slots:
    """Concurrency limit per worker process, created inside the running event loop."""

    semaphore: asyncio.Semaphore | None = None

    @classmethod
    def get(cls) -> asyncio.Semaphore:
        if cls.semaphore is None:
            cls.semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        return cls.semaphore


@app.middleware("http")
async def limit_body_size(request: Request, call_next: Any) -> Response:
    """Reject oversized uploads before their body is read."""
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > MAX_UPLOAD_BYTES + 64 * 1024:
        return JSONResponse(
            {"detail": f"File is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB"},
            status_code=413,
        )
    response: Response = await call_next(request)
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/v1/rules")
def rules() -> list[dict[str, str]]:
    return [
        {
            "id": r.id,
            "title": r.title,
            "layer": r.layer.value,
            "severity": r.severity.value,
            "description": r.description,
            "false_positives": r.false_positives,
        }
        for r in RULES.values()
    ]


async def _analyse(data: bytes, name: str, doc_type: str) -> Report:
    """Run the CPU-bound analysis on a worker thread.

    Python cannot kill a thread, so on timeout the client gets 504 while the thread finishes in
    the background. The concurrency slot is released only when the thread really ends, so slow
    uploads cannot pile up more work than ``TAMPERLINT_MAX_CONCURRENCY``.
    """
    slots = _Slots.get()
    await slots.acquire()
    task = asyncio.ensure_future(asyncio.to_thread(check, data, name=name, doc_type=doc_type))
    task.add_done_callback(lambda _t: slots.release())
    return await asyncio.wait_for(asyncio.shield(task), TIMEOUT_SECONDS)


@app.post("/v1/check", response_model=None)
async def check_endpoint(
    file: Annotated[UploadFile, File(description="PDF, JPEG, PNG, TIFF or WebP")],
    doc_type: Annotated[str, Query(pattern="^(auto|statement|invoice|generic)$")] = "auto",
    output: Annotated[Literal["json", "sarif", "html"], Query(alias="format")] = "json",
) -> Any:
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File is larger than {MAX_UPLOAD_BYTES // 1024 // 1024} MB")
    if not data:
        raise HTTPException(400, "Empty upload")
    name = os.path.basename(file.filename or "document")
    try:
        report = await _analyse(data, name, doc_type)
    except UnsupportedFileError as exc:
        # Messages are written by tamperlint for users; the file name is the client's own.
        raise HTTPException(415, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, "Analysis took too long") from exc
    except Exception as exc:
        log.exception("analysis failed for an upload")
        raise HTTPException(422, "The file could not be analysed.") from exc
    if output == "html":
        return HTMLResponse(render_html(report, data), headers=_HTML_HEADERS)
    if output == "sarif":
        return JSONResponse(to_sarif([report]))
    return JSONResponse(report.model_dump(mode="json"))
