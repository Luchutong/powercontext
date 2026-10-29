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
from collections import Counter
from collections.abc import Iterable, Mapping
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
# These are report-section aliases, independent of any company, document or question.
# A question may require several sections, including a statement that lacks a line item.
_EVIDENCE_HEADINGS = (
    (
        r"\b(?:income statements?|statements? of (?:income|earnings|operations)|p l|profit (?:and )?loss|"
        r"ebitda|revenue|sales|margin|net income|tax|capital intensive|capital intensity)\b",
        (
            "statement of income",
            "statements of income",
            "statement of earnings",
            "statements of earnings",
            "statement of operations",
            "statements of operations",
            "income statement",
            "income statements",
            "profit and loss",
        ),
    ),
    (
        r"\b(?:cash flows?|ebitda|capex|capital expenditures?|capital intensive|capital intensity)\b",
        ("statement of cash flows", "statements of cash flows", "cash flow statement", "cash flows"),
    ),
    (
        r"\b(?:balance sheets?|financial position|assets?|liabilities|equity|debt|current ratio|quick ratio|"
        r"ppne|ppe|p p e|capital intensive|capital intensity)\b",
        ("balance sheet", "balance sheets", "statement of financial position", "statements of financial position"),
    ),
    (
        r"\b(?:geograph\w*|countries|regions)\b",
        (
            "geographic areas",
            "geographical information",
            "geographic information",
            "geographic operations",
            "our operations",
            "business segments",
            "operating segments",
            "reportable segments",
            "segment information",
        ),
    ),
    (
        r"\b(?:legal|litigation|lawsuits?|battles?)\b",
        ("legal proceedings", "litigation", "commitments and contingencies", "contingencies"),
    ),
    (
        r"\b(?:segments?|categories)\b",
        ("business segment data", "segment information", "segment data", "reportable segments"),
    ),
)
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
    "able",
    "among",
    "based",
    "between",
    "change",
    "changed",
    "changes",
    "compare",
    "compared",
    "declined",
    "drove",
    "excluding",
    "healthy",
    "improved",
    "improving",
    "important",
    "least",
    "like",
    "major",
    "materially",
    "most",
    "much",
    "ongoing",
    "primarily",
    "primary",
    "profile",
    "reasonably",
    "relevant",
    "represent",
    "something",
    "subject",
    "than",
    "usd",
    "whether",
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

    async def collect(query: str, weight: float = 1.0) -> None:
        result = await memory.search(SearchMemoryRequest(query=query, mode="fts", limit=_SEARCH_POOL_LIMIT))
        for rank, hit in enumerate(result.hits, start=1):
            key = (
                hit.memory_ref.artifact_id,
                hit.memory_ref.revision,
                hit.entry_id,
                hit.entry_version_id,
            )
            score = weight / (_RRF_CONSTANT + rank)
            previous = ranked.get(key)
            ranked[key] = (hit, score + (0.0 if previous is None else previous[1]))

    for variant_index, variant in enumerate(variants):
        await collect(variant, 1.25 if variant_index == 0 else 1.0)
    if not ranked:
        return ()

    documents = _question_documents(question, tuple(hit.text for hit, _score in ranked.values()))
    if documents:
        # A document's projected identifier is searchable through the same scoped API.
        # Retrieve its complete page pool, including tables that omit the company's
        # name or use different financial terminology from the question.
        for document in sorted(documents):
            await collect(document)
        ranked = {key: value for key, value in ranked.items() if value[0].text.partition("\n")[0] in documents}

    # The first line is projection metadata, not the page body. Keep document targeting
    # separate so a company name on every page cannot outweigh the requested evidence.
    page_texts = {key: item[0].text.partition("\n")[2] for key, item in ranked.items()}
    text_terms = {key: Counter(_ranking_terms(text)) for key, text in page_texts.items()}
    company_terms = _company_ranking_terms(question, documents)
    expanded_terms = tuple(term for term in _expanded_ranking_terms(question) if term not in company_terms)
    query_terms = tuple(
        _normalize_ranking_term(term)
        for term in dict.fromkeys(analyze_fts_query(question).split())
        if not _is_low_value_term(term) and _normalize_ranking_term(term) not in company_terms
    )
    document_count = len(text_terms)
    document_frequency = {
        term: sum(term in candidate_terms for candidate_terms in text_terms.values()) for term in expanded_terms
    }
    term_weights = {
        term: math.log(1.0 + (document_count - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
        for term in expanded_terms
    }
    average_length = sum(sum(terms.values()) for terms in text_terms.values()) / document_count
    headings = _evidence_heading_groups(question)
    score_by_key = {
        key: (
            rrf
            + _lexical_score(text_terms[key], query_terms, expanded_terms, term_weights, average_length)
            + _heading_score(page_texts[key], headings)
            + _document_score(hit.text.partition("\n")[0], question)
        )
        for key, (hit, rrf) in ranked.items()
    }
    ordered = sorted(ranked, key=lambda key: (-score_by_key[key], key[0], key[2], key[3]))
    return await _exact_page_citations(memory, (ranked[key][0] for key in ordered), limit)


async def _exact_page_citations(
    memory: _ScopedPDFMemory, hits: Iterable[MemoryHit], limit: int
) -> tuple[MemoryCitation, ...]:
    citations: list[MemoryCitation] = []
    seen_source_ids: set[str] = set()
    for hit in hits:
        citation = MemoryCitation(
            memory_ref=hit.memory_ref,
            entry_id=hit.entry_id,
            entry_version_id=hit.entry_version_id,
        )
        record = await memory.get(GetMemoryEntryRequest(citation=citation))
        source_refs = tuple(source for source in record.entry.sources if source.source_type == PDF_PAGE_SOURCE_NAME)
        # A single citation must identify one physical page. If an extraction pipeline
        # consolidated identical page bodies, that ambiguous entry cannot prove a page.
        if len(source_refs) != 1 or source_refs[0].source_id in seen_source_ids:
            continue
        seen_source_ids.add(source_refs[0].source_id)
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
    variants.extend(_question_entities(question))
    for group in _evidence_heading_groups(question):
        variants.append(" ".join(dict.fromkeys(analyze_fts_query(" ".join(group)).split())))
    return tuple(dict.fromkeys(value for value in variants if value.strip()))


def _finance_synonym_queries(question: str) -> tuple[str, ...]:
    terms = set(analyze_fts_query(question).split())
    variants: list[str] = []
    for triggers, queries in (
        ({"capex"}, ("capital expenditures", "capital expenditure", "capital spending")),
        (
            {"ebitda", "unadjusted"},
            ("operating income depreciation amortization", "operating profit", "unadjusted operating income"),
        ),
        ({"geography", "geographies", "geographic", "geographical"}, ("geographic areas countries regions",)),
        (
            {"litigation", "legal", "lawsuit", "lawsuits", "battle", "battles"},
            ("legal proceedings", "litigation contingencies", "lawsuits claims"),
        ),
        ({"restructuring"}, ("restructuring charges", "restructuring costs")),
        ({"tax"}, ("income taxes effective tax rate",)),
        ({"cyclicality", "cyclical"}, ("cyclical economic demand",)),
        ({"retain", "retained", "retention"}, ("retention retain retained",)),
        ({"margin", "margins"}, ("operating income net revenue", "sales operating income", "operating profit")),
        ({"depreciation", "amortization"}, ("depreciation amortization",)),
        ({"ppne", "ppe"}, ("property plant equipment", "property equipment", "fixed assets")),
    ):
        if terms & triggers:
            variants.extend(queries)
    if re.search(r"\bd\s*&\s*a\b", question, re.IGNORECASE):
        variants.append("depreciation amortization")
    if "concentration" in terms and terms.intersection({"customer", "customers"}):
        variants.append("single customer one customer major customers")
    if "capital" in terms and terms.intersection({"intensive", "intensity"}):
        variants.extend(("capital expenditures depreciation", "property plant equipment", "capital spending"))
    if re.search(r"\bm\s*&\s*a\b", question, re.IGNORECASE) and terms.intersection({"exclude", "excluding"}):
        variants.extend(("organic sales growth", "organic local currency"))
    return tuple(variants)


def _expanded_ranking_terms(question: str) -> tuple[str, ...]:
    terms = list(analyze_fts_query(question).split())
    for query in _finance_synonym_queries(question):
        terms.extend(analyze_fts_query(query).split())
    for group in _evidence_heading_groups(question):
        terms.extend(analyze_fts_query(" ".join(group)).split())
    return tuple(
        dict.fromkeys(
            _normalize_ranking_term(term)
            for term in terms
            if not _is_low_value_term(term) and (len(term) > 1 or term.isdigit())
        )
    )


def _normalize_ranking_term(term: str) -> str:
    if term in {"retain", "retained", "retention"}:
        return "retain"
    if term in {"cyclical", "cyclicality"}:
        return "cyclical"
    if term in {"geography", "geographic", "geographical", "geographies"}:
        return "geography"
    if len(term) > 4 and term.endswith("ies"):
        return term[:-3] + "y"
    if len(term) > 4 and term.endswith("xes"):
        return term[:-2]
    if len(term) > 3 and term.endswith("s") and not term.endswith(("ss", "us")):
        return term[:-1]
    return term


def _ranking_terms(text: str) -> tuple[str, ...]:
    return tuple(_normalize_ranking_term(term) for term in analyze_text(text).split())


def _is_low_value_term(term: str) -> bool:
    return (
        term in _LOW_VALUE_TERMS
        or term in _QUERY_NOISE_TERMS
        or re.fullmatch(r"(?:fy?)?(?:19|20)\d{2}|fy\d{2}", term) is not None
    )


def _lexical_score(
    page_terms: Counter[str],
    original_terms: tuple[str, ...],
    expanded_terms: tuple[str, ...],
    weights: Mapping[str, float],
    average_length: float,
) -> float:
    if not expanded_terms:
        return 0.0
    original_set = set(original_terms)
    total_weight = sum(weights.values())
    length_normalization = 1.2 * (0.25 + 0.75 * sum(page_terms.values()) / max(average_length, 1.0))
    matched_weight = sum(
        weights[term]
        * (1.0 if term in original_set else 0.6)
        * (2.2 * page_terms[term] / (page_terms[term] + length_normalization))
        for term in expanded_terms
        if term in page_terms
    )
    return matched_weight / max(total_weight, 1.0)


def _evidence_heading_groups(question: str) -> tuple[tuple[str, ...], ...]:
    normalized = analyze_text(question)
    return tuple(headings for pattern, headings in _EVIDENCE_HEADINGS if re.search(pattern, normalized))


def _heading_score(text: str, groups: tuple[tuple[str, ...], ...]) -> float:
    # Match short heading lines rather than passing mentions buried in a long paragraph.
    # Also join adjacent lines to support titles wrapped by PDF text extraction.
    lines = text.splitlines()
    titles = [
        analyze_text(line)
        for line in (*lines, *(f"{left} {right}" for left, right in pairwise(lines)))
        if len(line) <= 160
    ]
    return 0.7 * sum(
        any(heading in title and len(heading) >= len(title) * 0.5 for heading in group for title in titles)
        for group in groups
    )


def _document_score(document_id: str, question: str) -> float:
    company = re.split(r"[\W_]+(?:19|20)\d{2}", document_id.casefold(), maxsplit=1)[0]
    name = re.sub(r"[\W_]+", "", company)
    normalized_question = re.sub(r"[\W_]+", "", question.casefold())
    if not name or name not in normalized_question:
        return 0.0
    if len(name) < 3 and name not in re.findall(r"[a-z0-9]+", question.casefold()):
        return 0.0
    years = set(re.findall(r"(?:19|20)\d{2}", document_id))
    question_years = set(re.findall(r"(?:19|20)\d{2}", question))
    return 0.4 + (0.2 if years & question_years else 0.0)


def _question_entities(question: str) -> tuple[str, ...]:
    meaningful = set(analyze_fts_query(question).split())
    return tuple(
        dict.fromkeys(
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9]+", question)
            if any(character.isupper() for character in token)
            and len(token) > 1
            and token.casefold() in meaningful
            and not _is_low_value_term(token.casefold())
            and token.casefold() not in {"ebitda", "ebit", "capex", "eps", "gaap", "roe", "roa", "ppne", "ppe"}
        )
    )[:6]


def _question_documents(question: str, texts: tuple[str, ...]) -> frozenset[str]:
    documents = {text.partition("\n")[0] for text in texts}
    named = frozenset(document for document in documents if _document_score(document, question))
    if named:
        return named
    entities = _question_entities(question)
    matches: dict[str, set[str]] = {entity: set() for entity in entities}
    for text in texts:
        document, _separator, body = text.partition("\n")
        terms = set(analyze_text(body).split())
        for entity in entities:
            if entity in terms:
                matches[entity].add(document)
    # Resolve an abbreviation only when the retrieved corpus identifies one document.
    # Ambiguous common words do not narrow the caller's scope.
    unique = set().union(*(values for values in matches.values() if len(values) == 1))
    return frozenset(unique) if len(unique) == 1 else frozenset()


def _company_ranking_terms(question: str, documents: frozenset[str]) -> frozenset[str]:
    if not documents:
        return frozenset()
    names = [
        re.sub(r"[\W_]+", "", re.split(r"[\W_]+(?:19|20)\d{2}", document.casefold(), maxsplit=1)[0])
        for document in documents
    ]
    terms = frozenset(
        _normalize_ranking_term(term)
        for term in analyze_fts_query(question).split()
        if len(term) >= 2 and any(term in name for name in names)
    )
    # When document identity was inferred from an unambiguous abbreviation, exclude
    # that abbreviation too. Company mentions route retrieval; they are not page evidence.
    return terms or frozenset(_normalize_ranking_term(term) for term in _question_entities(question))


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
