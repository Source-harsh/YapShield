"""Loopback HTTP interface for document review and release."""

import asyncio
import base64
import re
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .detection import MAX_CANDIDATES, Mention, detect
from .documents import export_pdf, extract, redact, summarize, validate

MAX_BYTES = 8 * 1024 * 1024
TTL_SECONDS = 15 * 60
MAX_REVIEWS = 8
DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


@dataclass
class Review:
    text: str
    groups: list[list[Mention]]
    expires: float


reviews: dict[str, Review] = {}


def expire_reviews() -> None:
    now = monotonic()
    for key in [key for key, review in reviews.items() if review.expires <= now]:
        del reviews[key]


async def cleanup() -> None:
    while True:
        await asyncio.sleep(15)
        expire_reviews()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(cleanup())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        reviews.clear()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])


@app.middleware("http")
async def protect_responses(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    if request.url.path.startswith("/api/") and request.headers.get("sec-fetch-site") == "cross-site":
        return Response(status_code=403, headers={"Cache-Control": "no-store"})
    try:
        response = await call_next(request)
    finally:
        # Alias and manual patterns can contain document text.
        if request.url.path.startswith("/api/"):
            re.purge()
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


class ReleaseRequest(BaseModel):
    decisions: Annotated[dict[int, Literal["redact", "keep"]], Field(max_length=MAX_CANDIDATES)]
    manual: Annotated[list[Annotated[str, Field(min_length=2, max_length=500)]], Field(max_length=50)]


ReviewToken = Annotated[str, Header(alias="X-Review-Token", min_length=1, max_length=100)]


async def bounded_body(request: Request, limit: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, "The request exceeds the supported size.")
        body.extend(chunk)
    return bytes(body)


@app.post("/api/review")
async def create_review(request: Request) -> dict:
    content_type = request.headers.get("content-type", "").split(";")[0]
    if content_type not in {"application/pdf", "text/plain"}:
        raise HTTPException(415, "Provide a TXT or text-based PDF.")
    data = await bounded_body(request, MAX_BYTES)
    expire_reviews()
    if len(reviews) >= MAX_REVIEWS:
        raise HTTPException(429, "Close an existing review or wait for it to expire.")
    try:
        source = extract(data, pdf=content_type == "application/pdf")
        groups = detect(source)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    token = secrets.token_urlsafe(32)
    reviews[token] = Review(source, groups, monotonic() + TTL_SECONDS)
    return {
        "token": token,
        "text": source,
        "groups": [
            {
                "id": index,
                "kind": group[0].kind,
                "mentions": [
                    {"text": source[item.start:item.end], "source": item.source, "line": source.count("\n", 0, item.start) + 1}
                    for item in group
                ],
            }
            for index, group in enumerate(groups)
        ],
    }


@app.delete("/api/review", status_code=204)
async def discard_review(token: ReviewToken) -> Response:
    reviews.pop(token, None)
    return Response(status_code=204)


@app.post("/api/release")
async def release(request: Request, token: ReviewToken) -> JSONResponse:
    body = await bounded_body(request, 160 * 1024)
    expire_reviews()
    review = reviews.get(token)
    if review is None:
        raise HTTPException(404, "This review expired or was closed. Upload a fresh copy.")
    try:
        selection = ReleaseRequest.model_validate_json(body)
    except ValueError:
        raise HTTPException(422, "The release decisions could not be read.") from None
    if selection.decisions.keys() != set(range(len(review.groups))):
        raise HTTPException(422, "Decide whether to redact or keep every candidate before release.")
    ranges = {
        (item.start, item.end)
        for index, group in enumerate(review.groups)
        if selection.decisions[index] == "redact"
        for item in group
    }
    for value in selection.manual:
        if not value.strip():
            raise HTTPException(422, "Enter a nonblank manual redaction.")
        pattern = re.compile(r"(?=(" + re.escape(value) + r"))", re.IGNORECASE)
        if not pattern.search(review.text):
            raise HTTPException(422, "A manual redaction does not appear in the extracted text.")
        ranges.update(match.span(1) for match in pattern.finditer(review.text))
    sanitized, removed = redact(review.text, ranges)
    try:
        validate(sanitized, removed)
        summary = summarize(sanitized)
        validate(summary, removed)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    pdf, pdf_error = None, None
    try:
        pdf = base64.b64encode(export_pdf(sanitized, removed)).decode("ascii")
    except ValueError as error:
        pdf_error = str(error)
    reviews.pop(token, None)
    return JSONResponse({"text": sanitized, "summary": summary, "pdf": pdf, "pdf_error": pdf_error})


if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
