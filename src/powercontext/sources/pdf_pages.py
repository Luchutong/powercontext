# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Captured physical-page Sources for complete offline PDF documents."""

from __future__ import annotations

import hashlib
import io
import math
import re
from collections.abc import Mapping
from itertools import pairwise
from typing import Protocol, cast
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, StrictInt, field_validator, model_validator
from pypdf import PdfReader

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.artifacts.search import analyze_fts_query, analyze_text
from powercontext.builtin.runtime import GetMemoryEntryRequest, SearchMemoryRequest
from powercontext.builtin.runtime.models import MemoryEntryRecord, MemoryHit, MemorySearchPage
from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, SourceMaterialization, TextEvidence
from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source

PDF_PAGE_SOURCE_NAME = "pdf-page"
_LOWER_HEX_64 = re.compile(r"[0-9a-f]{64}\Z")
_SEARCH_POOL_LIMIT = 1024
_MAX_PAIR_VARIANTS = 14
_RRF_CONSTANT = 60
_LOW_VALUE_TERMS = frozenset({
    "annual",
    "company",
    "financial",
    "fiscal",
    "fy",
    "report",
    "reported",
    "statement",
    "statements",
    "year",
})
_QUERY_NOISE_TERMS = frozenset({
    "answer",
    "assuming",
    "calculate",
    "company",
    "define",
    "details",
    "directly",
    "explicitly",
    "fiscal",
    "fy",
    "give",
    "has",
    "if",
    "millions",
    "only",
    "perspective",
    "quantity",
    "question",
    "any",
    "relying",
    "reported",
    "reporting",
    "respond",
    "response",
    "shown",
    "state",
    "statement",
    "statements",
    "then",
    "use",
    "using",
    "within",
    "year",
    "outlined",
})


class PDFDocumentInput(BaseModel):
    """Complete PDF bytes and the caller's stable document identifier."""

    document_id: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        _validate_document_id(value)
        return value

    @field_validator("content", mode="before")
    @classmethod
    def validate_content_type(cls, value: object) -> object:
        if not isinstance(value, bytes):
            raise ValueError("content must be bytes")  # noqa: TRY003, TRY004
        return value


class PDFPageInput(BaseModel):
    """One extracted physical page with its complete document revision identity."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    document_sha256: str
    page_number: StrictInt
    text: str

    @model_validator(mode="after")
    def validate_page(self) -> PDFPageInput:
        _validate_document_id(self.document_id)
        if not _LOWER_HEX_64.fullmatch(self.document_sha256):
            raise ValueError("document_sha256 must be 64 lowercase hexadecimal characters")  # noqa: TRY003
        if self.page_number < 1:
            raise ValueError("page_number must be a positive physical page number")  # noqa: TRY003
        pdf_page_source_id(self.document_id, self.document_sha256, self.page_number)
        return self


class PDFPageSource(Source):
    """A captured PDF page whose identity includes its document revision and page."""

    materialization: SourceMaterialization = SourceMaterialization.CAPTURED
    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str

    @field_validator("materialization")
    @classmethod
    def require_captured_materialization(cls, value: SourceMaterialization) -> SourceMaterialization:
        if value is not SourceMaterialization.CAPTURED:
            raise ValueError("PDF page Sources must be captured")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def validate_page_source(self) -> PDFPageSource:
        expected_id = pdf_page_source_id(self.document_id, self.document_sha256, self.page_number)
        if self.source_id != expected_id or self.name != expected_id:
            raise ValueError("name and source_id must match the PDF page identity")  # noqa: TRY003
        return self


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page atomically, including pages with no text."""

    if not value.content:
        raise ValueError("PDF content must not be empty")  # noqa: TRY003

    document_sha256 = hashlib.sha256(value.content).hexdigest()
    try:
        reader = PdfReader(io.BytesIO(value.content), strict=True)
    except Exception as error:
        raise ValueError("PDF content is damaged or could not be completely parsed") from error  # noqa: TRY003
    if reader.is_encrypted:
        try:
            password_type = reader.decrypt("")
        except Exception as error:
            raise ValueError("PDF content is damaged or could not be completely parsed") from error  # noqa: TRY003
        if not password_type:
            raise ValueError("PDF is encrypted with a non-empty password")  # noqa: TRY003

    try:
        pages = tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=document_sha256,
                page_number=index,
                text=page.extract_text() or "",
            )
            for index, page in enumerate(reader.pages, start=1)
        )
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("PDF content is damaged or could not be completely parsed") from error  # noqa: TRY003

    return pages


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Return a stable, delimiter-safe Source name for one document revision page."""

    _validate_document_id(document_id)
    if not _LOWER_HEX_64.fullmatch(document_sha256):
        raise ValueError("document_sha256 must be 64 lowercase hexadecimal characters")  # noqa: TRY003
    if not isinstance(page_number, int) or isinstance(page_number, bool) or page_number < 1:
        raise ValueError("page_number must be a positive physical page number")  # noqa: TRY003

    encoded_id = quote(document_id, safe="")
    source_id = f"pdf:{encoded_id}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        compact_id = hashlib.sha256(document_id.encode("utf-8")).hexdigest()
        source_id = f"pdf-sha256:{compact_id}:{document_sha256}:{page_number}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("PDF page identity exceeds the Source identifier limit")  # noqa: TRY003
    return source_id


class PDFPageTextEvidenceProjection:
    """Project one page's full body and exact document-location metadata."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


class PDFPageSourceDefinition:
    """Resolve, read, and project captured PDF page inputs."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    version = "1"
    source_class = PDFPageSource
    projections: tuple[SourceProjection[PDFPageSource], ...]

    def __init__(self) -> None:
        self.projections = (cast(SourceProjection[PDFPageSource], PDFPageTextEvidenceProjection()),)

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class _ScopedPDFMemory(Protocol):
    async def search(self, request: SearchMemoryRequest, /) -> MemorySearchPage: ...

    async def get(self, request: GetMemoryEntryRequest, /) -> MemoryEntryRecord: ...


async def search_pdf_pages(
    memory: _ScopedPDFMemory,
    question: str,
    limit: int = 10,
) -> tuple[MemoryCitation, ...]:
    """Search scoped page evidence and return distinct, exact Memory citations."""

    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must contain non-whitespace text")  # noqa: TRY003
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003

    variants = _search_variants(question)
    ranked: dict[tuple[str, int, str, str], tuple[MemoryHit, float]] = {}
    for variant_index, variant in enumerate(variants):
        result = await memory.search(SearchMemoryRequest(query=variant, mode="fts", limit=_SEARCH_POOL_LIMIT))
        for rank, hit in enumerate(result.hits, start=1):
            key = (
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            rrf_weight = 1.25 if variant_index == 0 else (0.75 if variant_index >= len(variants) - 5 else 1.0)
            score = rrf_weight / (_RRF_CONSTANT + rank)
            previous = ranked.get(key)
            ranked[key] = (hit, score + (0.0 if previous is None else previous[1]))

    if not ranked:
        return ()

    text_terms = {key: set(analyze_text(item[0].text).split()) for key, item in ranked.items()}
    expanded_terms = _expanded_ranking_terms(question)
    query_terms = tuple(
        term for term in dict.fromkeys(analyze_fts_query(question).split()) if not _is_low_value_term(term)
    )
    document_count = len(text_terms)
    document_frequency = {
        term: sum(term in candidate_terms for candidate_terms in text_terms.values()) for term in expanded_terms
    }
    term_weights = {
        term: math.log((document_count + 1) / (document_frequency[term] + 1)) + 1.0 for term in expanded_terms
    }
    score_by_key = {
        key: rrf + _lexical_score(text_terms[key], query_terms, expanded_terms, term_weights)
        for key, (hit, rrf) in ranked.items()
    }
    ordered = sorted(ranked, key=lambda key: (-score_by_key[key], key[0], key[2], key[3]))

    citations: list[MemoryCitation] = []
    seen_source_ids: set[str] = set()
    seen_citations: set[tuple[str, int, str, str]] = set()
    for key in ordered:
        hit = ranked[key][0]
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        if key in seen_citations:
            continue
        record = await memory.get(GetMemoryEntryRequest(citation=citation))
        source_refs = tuple(source for source in record.entry.sources if source.source_type == PDF_PAGE_SOURCE_NAME)
        # A single citation must identify one physical page. If an extraction pipeline
        # consolidated identical page bodies, that ambiguous entry cannot prove a page.
        if len(source_refs) != 1 or source_refs[0].source_id in seen_source_ids:
            continue
        seen_source_ids.add(source_refs[0].source_id)
        seen_citations.add(key)
        citations.append(citation)
        if len(citations) >= limit:
            break

    return tuple(citations)


def _search_variants(question: str) -> tuple[str, ...]:
    variants = [question.strip()]
    terms = tuple(
        dict.fromkeys(
            term
            for term in analyze_fts_query(question).split()
            if not _is_low_value_term(term) and (len(term) > 1 or term.isdigit())
        )
    )
    if len(terms) <= 4:
        if len(terms) >= 2:
            variants.append(" ".join(terms))
    else:
        start_positions = list(range(0, len(terms) - 3, 3))
        final_start = len(terms) - 4
        if final_start not in start_positions:
            start_positions.append(final_start)
        variants.extend(" ".join(terms[start : start + 4]) for start in start_positions[:_MAX_PAIR_VARIANTS])
    variants.extend(_finance_synonym_queries(question))
    return tuple(dict.fromkeys(value for value in variants if value.strip()))


def _finance_synonym_queries(question: str) -> tuple[str, ...]:
    terms = set(analyze_fts_query(question).split())
    variants: list[str] = []
    if "capex" in terms:
        variants.extend(("capital expenditures", "capital expenditure"))
    if terms.intersection({"ebitda", "unadjusted"}):
        variants.extend(("operating income depreciation amortization", "unadjusted operating income"))
    if terms.intersection({"geography", "geographies", "geographic", "geographical"}):
        variants.append("geographic areas countries regions")
    if terms.intersection({"litigation", "legal", "lawsuit", "lawsuits", "battle", "battles"}):
        variants.extend(("legal proceedings", "litigation contingencies", "lawsuits claims"))
    if "restructuring" in terms:
        variants.extend(("restructuring charges", "restructuring costs"))
    if terms.intersection({"margin", "margins"}):
        variants.extend(("operating income net revenue", "sales operating income"))
    if terms.intersection({"depreciation", "amortization"}) or terms.intersection({"d", "a"}):
        variants.append("depreciation amortization")
    return tuple(variants)


def _expanded_ranking_terms(question: str) -> tuple[str, ...]:
    terms = list(analyze_fts_query(question).split())
    for query in _finance_synonym_queries(question):
        terms.extend(analyze_fts_query(query).split())
    return tuple(
        dict.fromkeys(term for term in terms if not _is_low_value_term(term) and (len(term) > 1 or term.isdigit()))
    )


def _is_low_value_term(term: str) -> bool:
    return (
        term in _LOW_VALUE_TERMS
        or term in _QUERY_NOISE_TERMS
        or term in {"2021", "2022", "2023", "2024", "2025"}
        or re.fullmatch(r"fy?20\d{2}", term) is not None
    )


def _lexical_score(
    page_terms: set[str],
    original_terms: tuple[str, ...],
    expanded_terms: tuple[str, ...],
    weights: Mapping[str, float],
) -> float:
    if not expanded_terms:
        return 0.0
    original_set = set(original_terms)
    total_weight = sum(weights.values())
    matched_weight = sum(
        weights[term] * (1.0 if term in original_set else 0.6) for term in expanded_terms if term in page_terms
    )
    score = matched_weight / max(total_weight, 1.0)
    if len(original_terms) > 1:
        adjacent_matches = sum(left in page_terms and right in page_terms for left, right in pairwise(original_terms))
        score += 0.02 * adjacent_matches
    return score


def _validate_document_id(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("document_id must not be empty or whitespace-only")  # noqa: TRY003


PDF_PAGE_SOURCE_DEFINITION = cast(
    SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput],
    PDFPageSourceDefinition(),
)


__all__ = [
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageSourceDefinition",
    "PDFPageTextEvidenceProjection",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
