"""Loopback HTTP interface for document review and release."""

import asyncio
import base64
import hashlib
import json
import re
import secrets
from bisect import bisect_right
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

from .detection import MAX_CANDIDATES, Mention, detect_mentions
from .documents import (
    PdfLayoutError, canonical, count_value, export_pdf, extract, redact, residual_key, summarize, validate, verify_txt,
)
from .identity import Cluster, Relationship, mention_id, resolve
from .summary_privacy import check_summary

MAX_BYTES = 8 * 1024 * 1024
TTL_SECONDS = 15 * 60
MAX_REVIEWS = 8
DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


@dataclass
class Review:
    text: str
    groups: list[list[Mention]]
    expires: float
    clusters: dict[Mention, Cluster]
    preview: tuple[str, bytes] | None = None


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


Decision = Literal["redact", "keep"]


class LinkDecision(BaseModel):
    status: Literal["confirm", "reject", "detach"]
    decision: Decision | None = None


class ReleaseRequest(BaseModel):
    decisions: Annotated[dict[int, Decision], Field(max_length=MAX_CANDIDATES)] = Field(default_factory=dict)
    manual: Annotated[list[Annotated[str, Field(min_length=2, max_length=500)]], Field(max_length=50)]
    links: Annotated[dict[str, LinkDecision], Field(max_length=MAX_CANDIDATES)] | None = None
    mention_decisions: Annotated[dict[str, Decision], Field(max_length=MAX_CANDIDATES)] | None = None


class DetectionEvidence(BaseModel):
    source: str
    rule: str


class ReviewMention(BaseModel):
    id: str
    text: str
    kind: str
    start: int
    end: int
    source: str
    line: int
    evidence: list[DetectionEvidence]


class ReviewGroup(BaseModel):
    id: int
    kind: str
    mentions: list[ReviewMention]
    identity: "IdentityCluster | None" = None


class IdentityLink(BaseModel):
    mention_id: str
    relationship: Relationship
    evidence: str
    uncertainty: Literal["proposed"] = "proposed"
    status: Literal["pending"] = "pending"


class IdentityCluster(BaseModel):
    id: str
    primary_mention_id: str
    mention_ids: list[str]
    alternates: list[IdentityLink]
    relationship: Relationship
    possible_primary_ids: list[str]
    possible_primary_count: int
    evidence: str
    uncertainty: Literal["unlinked", "proposed", "uncertain"]
    status: Literal["pending", "separate"]


class ReviewResponse(BaseModel):
    token: str
    text: str
    groups: list[ReviewGroup]


ReviewToken = Annotated[str, Header(alias="X-Review-Token", min_length=1, max_length=100)]


async def bounded_body(request: Request, limit: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, "The request exceeds the supported size.")
        body.extend(chunk)
    return bytes(body)


@app.post("/api/review", response_model=ReviewResponse)
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
        groups, clusters = resolve(source, detect_mentions(source))
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    token = secrets.token_urlsafe(32)
    reviews[token] = Review(source, groups, monotonic() + TTL_SECONDS, clusters)
    line_starts = [0, *(match.end() for match in re.finditer("\n", source))]
    return {
        "token": token,
        "text": source,
        "groups": [
            {
                "id": index,
                "kind": group[0].kind,
                "identity": serialize_cluster(clusters[group[0]]) if group[0] in clusters else None,
                "mentions": [
                    {
                        "id": mention_id(item),
                        "text": source[item.start:item.end],
                        "kind": item.kind,
                        "start": item.start,
                        "end": item.end,
                        "source": item.source,
                        "line": bisect_right(line_starts, item.start),
                        "evidence": [evidence._asdict() for evidence in item.evidence],
                    }
                    for item in group
                ],
            }
            for index, group in enumerate(groups)
        ],
    }


def serialize_cluster(cluster: Cluster) -> IdentityCluster:
    primary = mention_id(cluster.primary)
    return IdentityCluster(
        id=f"identity:{primary}", primary_mention_id=primary,
        mention_ids=[primary, *(mention_id(link.mention) for link in cluster.alternates)],
        alternates=[IdentityLink(mention_id=mention_id(link.mention), relationship=link.relationship,
                                 evidence=link.evidence) for link in cluster.alternates],
        relationship=cluster.relationship,
        possible_primary_ids=[mention_id(item) for item in cluster.possible_primaries[:8]],
        possible_primary_count=len(cluster.possible_primaries), evidence=cluster.evidence,
        uncertainty="uncertain" if cluster.possible_primaries else "proposed" if cluster.alternates else "unlinked",
        status="pending" if cluster.alternates else "separate",
    )


def selected_mentions(review: Review, selection: ReleaseRequest) -> tuple[set[tuple[int, int]], set[Mention]]:
    ranges: set[tuple[int, int]] = set()
    kept: set[Mention] = set()
    links = selection.links
    expected = {mention_id(link.mention) for cluster in review.clusters.values() for link in cluster.alternates}
    if "mention_decisions" in selection.model_fields_set:
        mentions = {mention_id(item): item for group in review.groups for item in group}
        decisions = selection.mention_decisions
        if decisions is None or decisions.keys() != mentions.keys() or selection.decisions:
            raise HTTPException(422, "Provide a separate redaction decision for every mention.")
        if links is None or links.keys() != expected or any(link.decision is not None for link in links.values()):
            raise HTTPException(422, "Resolve each proposed identity link separately from its redaction decision.")
        return ({(item.start, item.end) for key, item in mentions.items() if decisions[key] == "redact"},
                {item for key, item in mentions.items() if decisions[key] == "keep"})

    if selection.decisions.keys() != set(range(len(review.groups))):
        raise HTTPException(422, "Decide whether to redact or keep every candidate before release.")
    if links is None and "links" in selection.model_fields_set:
        raise HTTPException(422, "Provide link decisions as an object.")
    if (links or {}).keys() != expected:
        raise HTTPException(422, "Resolve every proposed link before release.")
    for index, group in enumerate(review.groups):
        cluster = review.clusters.get(group[0])
        for item in group:
            decision = selection.decisions[index]
            if links is not None and cluster is not None and item != cluster.primary:
                link = links[mention_id(item)]
                if link.status == "confirm" and link.decision is not None:
                    raise HTTPException(422, "A confirmed mention uses its identity decision.")
                if link.status != "confirm":
                    if link.decision is None:
                        raise HTTPException(422, "Choose whether to redact or keep each separate mention.")
                    decision = link.decision
            if decision == "redact":
                ranges.add((item.start, item.end))
            else:
                kept.add(item)
    return ranges, kept


def retained_ranges(kept: set[Mention]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for start, end, *_ in sorted(kept):
        if ranges and start < ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], max(end, ranges[-1][1]))
        else:
            ranges.append((start, end))
    return ranges


@app.delete("/api/review", status_code=204)
async def discard_review(token: ReviewToken) -> Response:
    reviews.pop(token, None)
    return Response(status_code=204)


async def read_selection(request: Request, token: str) -> tuple[Review, ReleaseRequest]:
    body = await bounded_body(request, 160 * 1024)
    expire_reviews()
    review = reviews.get(token)
    if review is None:
        raise HTTPException(404, "This review expired or was closed. Upload a fresh copy.")
    try:
        return review, ReleaseRequest.model_validate_json(body)
    except ValueError:
        raise HTTPException(422, "The release decisions could not be read.") from None


@dataclass
class PreparedRelease:
    text: str
    removed: set[str]
    allowed: dict[str, int]
    required: dict[str, int]
    summary_input: str
    summary_blocked: set[str]


def prepare_redactions(review: Review, selection: ReleaseRequest) -> PreparedRelease:
    ranges, kept = selected_mentions(review, selection)
    for value in selection.manual:
        if not value.strip():
            raise HTTPException(422, "Enter a nonblank manual redaction.")
        pattern = re.compile(r"(?=(" + re.escape(value) + r"))", re.IGNORECASE)
        if not pattern.search(review.text):
            raise HTTPException(422, "A manual redaction does not appear in the extracted text.")
        ranges.update(match.span(1) for match in pattern.finditer(review.text))
    retained = retained_ranges(kept)
    retained_ends = [end for _, end in retained]
    if retained:
        for start, end in ranges:
            next_retained = bisect_right(retained_ends, start)
            if next_retained < len(retained) and retained[next_retained][0] < end:
                raise HTTPException(422, "An approved redaction overlaps a mention marked Keep. Review the decisions.")
    allowed = {}
    required: dict[str, int] = {}
    try:
        sanitized, removed = redact(review.text, ranges)
        masked, summary_blocked = redact(review.text, ranges.union(retained)) if retained else (sanitized, removed)
        # Overlapping detector types may refer to the same kept occurrence.
        for start, end in {(item.start, item.end) for item in kept}:
            value = canonical(review.text[start:end])
            required[value] = required.get(value, 0) + 1
        if retained and removed:
            validate(masked, removed)
            values = [residual_key(review.text[start:end]) for start, end in retained]
            allowed = {needle: sum(count_value(value, needle) for value in values)
                       for needle in {residual_key(value) for value in removed}}
        validate(sanitized, removed, allowed=allowed, required=required)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    return PreparedRelease(sanitized, removed, allowed, required, masked, summary_blocked)


def selection_digest(selection: ReleaseRequest) -> bytes:
    # Only the digest and the opaque receipt are retained with the review.
    state = selection.model_dump(exclude_unset=True)
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).digest()


@app.post("/api/preview")
async def preview(request: Request, token: ReviewToken) -> dict[str, str]:
    review, selection = await read_selection(request, token)
    review.preview = None
    prepared = prepare_redactions(review, selection)
    receipt = secrets.token_urlsafe(32)
    review.preview = receipt, selection_digest(selection)
    return {"text": prepared.text, "receipt": receipt}


@app.post("/api/release")
async def release(request: Request, token: ReviewToken) -> JSONResponse:
    review, selection = await read_selection(request, token)
    receipt = request.headers.get("X-Preview-Receipt")
    if review.preview is None or review.preview[0] != receipt:
        raise HTTPException(409, "Preview the current review decisions before release.")
    if review.preview[1] != selection_digest(selection):
        review.preview = None
        raise HTTPException(409, "Preview the current review decisions before release.")
    prepared = prepare_redactions(review, selection)
    checks = {"allowed": prepared.allowed, "required": prepared.required}
    txt = prepared.text.encode("utf-8")
    try:
        sanitized = verify_txt(txt, prepared.text, prepared.removed, **checks)
        pdf, pdf_error = None, None
        try:
            pdf = base64.b64encode(export_pdf(sanitized, prepared.removed, **checks)).decode("ascii")
        except PdfLayoutError as error:
            pdf_error = str(error)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(422, "The generated files failed release verification.") from error
    try:
        validate(prepared.summary_input, prepared.summary_blocked)
        summary = summarize(prepared.summary_input)
        check_summary(summary, prepared.summary_input, prepared.summary_blocked)
    except (ValueError, RuntimeError):
        summary = ""
    reviews.pop(token, None)
    return JSONResponse({"text": sanitized, "txt": base64.b64encode(txt).decode("ascii"),
                         "summary": summary, "pdf": pdf, "pdf_error": pdf_error})


if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")
