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

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


class _PagePipeline:
    async def extract(self, request, /):
        return tuple(
            MemoryEntryInput(
                kind="source-evidence",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                sources=(source,),
            )
            for source in request.sources
        )


def test_financial_statement_evidence_survives_long_keyword_distractors_and_restart(tmp_path: Path) -> None:
    pytest.importorskip("pypdf", reason="PDF Sources require the parser provided by the competition environment")
    from powercontext.sources.pdf_pages import PDF_PAGE_SOURCE_DEFINITION, PDFPageInput, search_pdf_pages

    async def run() -> None:
        texts = [
            "Consolidated Statements of Earnings\nExample Foods Inc.\nFiscal years 2019 and 2018\n"
            "Net sales 120 110\nOperating profit 30 25\nIncome taxes 5 4\nNet earnings 25 21",
            "Consolidated Statements of Cash Flows\nExample Foods Inc.\nFiscal years 2019 and 2018\n"
            "Depreciation and amortization 10 9\nCapital spending 15 12\nOperating activities 40 35",
            "Consolidated Balance Sheets\nExample Foods Inc.\nFiscal years 2019 and 2018\n"
            "Property, plant and equipment, net 30 25\nTotal assets 100 90",
        ]
        texts.extend(
            f"Management review {number}\nExample Foods Inc. discusses EBITDA, capex, operating income, "
            "cash flow, restructuring costs and financial statements. "
            + "Business performance and investment outlook. "
            * 80
            for number in range(24)
        )
        target_document = "EXAMPLE_FOODS_2019_10K"
        inputs = [
            PDFPageInput(document_id=target_document, document_sha256="a" * 64, page_number=number, text=text)
            for number, text in enumerate(texts, 1)
        ]
        # The same statements in other companies and years must not crowd out the
        # requested report. No question or page identity comes from competition labels.
        inputs.extend(
            PDFPageInput(
                document_id=document,
                document_sha256="b" * 64,
                page_number=number,
                text=text.replace("Example Foods", company).replace("2019", "2018") + f"\nSupplement {number}",
            )
            for document, company in (
                ("EXAMPLE_FOODS_2018_10K", "Example Foods"),
                ("OTHER_FOODS_2018_10K", "Other Foods"),
            )
            for number, text in enumerate(texts[:2] * 6, 1)
        )
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pages.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="PDF evidence", summary="Statement retrieval", idempotency_key="pdf")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for page in inputs:
                source = await registry.resolve(page)
                locations[source.name] = (page.document_id, page.page_number)
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass

        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            questions = (
                (
                    "What was Example Foods' EBITDA less capex in FY2019? Use the income statement "
                    "and the statement of cash flows.",
                    {1, 2},
                ),
                ("What restructuring costs appear directly in Example Foods' income statement for FY2019?", {1}),
                ("Did Example Foods grow its PPNE between FY2018 and FY2019?", {3}),
                ("Is Example Foods a capital-intensive business based on FY2019 data?", {2, 3}),
            )
            for question, expected in questions:
                citations = await search_pdf_pages(memory, question, limit=20)
                pages = set()
                for citation in citations:
                    record = await memory.get(GetMemoryEntryRequest(citation=citation))
                    assert len(record.entry.sources) == 1
                    pages.add(locations[record.entry.sources[0].source_id])
                assert len(pages) == len(citations) <= 20
                assert {(target_document, number) for number in expected} <= pages

    asyncio.run(run())


@pytest.mark.parametrize(
    ("document_id", "company"),
    [("NORTHCOASTTECH_2022_10K", "NCT"), ("7LAB_2022_10K", "7Lab")],
)
def test_company_identity_survives_acronyms_and_numeric_names(tmp_path: Path, document_id: str, company: str) -> None:
    pytest.importorskip("pypdf")
    from powercontext.sources.pdf_pages import PDF_PAGE_SOURCE_DEFINITION, PDFPageInput, search_pdf_pages

    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'companies.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        pages = [
            (document_id, 1, f"Company Overview\n{company} is our company's abbreviation."),
            (document_id, 2, "Sales concentration\nA single buyer accounted for 80% of turnover."),
            *[
                (
                    "OTHER_2022_10K",
                    number,
                    "Customer concentration report\n"
                    f"No customer concentration was reported in fiscal year 2022. Management discussion {number}.",
                )
                for number in range(1, 25)
            ],
        ]
        locations = {}
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Companies", summary="Acronym and numeric identity", idempotency_key="companies")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for document, number, text in pages:
                source = await registry.resolve(
                    PDFPageInput(document_id=document, document_sha256="a" * 64, page_number=number, text=text)
                )
                locations[source.name] = (document, number)
                await runtime.ingestion.submit(
                    SubmitSourceObservation(
                        scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                    )
                )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(
                memory, f"Does {company} report customer concentration for FY2022?", limit=20
            )
            found = set()
            for citation in citations:
                record = await memory.get(GetMemoryEntryRequest(citation=citation))
                found.update(locations[source.source_id] for source in record.entry.sources)
            assert (document_id, 2) in found
            assert len(found) == len(citations) <= 20

    asyncio.run(run())


def test_complete_dense_pdf_page_is_persisted_and_read_after_restart(tmp_path: Path) -> None:
    pytest.importorskip("pypdf")
    from powercontext.sources.pdf_pages import PDF_PAGE_SOURCE_DEFINITION, PDFPageInput, search_pdf_pages

    async def run() -> None:
        text = "Full financial disclosure with complete table numbers 12345. " * 180
        assert len(text.encode()) > 8192
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'dense.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        page = PDFPageInput(document_id="DENSE_2022_10K", document_sha256="a" * 64, page_number=1, text=text)
        source = await registry.resolve(page)
        async with open_builtin_runtime(config, candidate_pipeline=_PagePipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Dense page", summary="Full PDF page evidence", idempotency_key="dense")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            await runtime.ingestion.submit(
                SubmitSourceObservation(
                    scope_id=scope.scope_id, observation=project_source_for_transport(registry, source)
                )
            )
            memory = runtime.memory.for_scope(scope.scope_id)
            while (await memory.flush()).processed:
                pass
        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(memory, "Find DENSE's complete financial disclosure for 2022", limit=20)
            assert len(citations) == 1
            entry = (await memory.get(GetMemoryEntryRequest(citation=citations[0]))).entry
            assert entry.text == f"{page.document_id}\n{page.text}".strip()
            assert entry.sources[0].source_id == source.name

    asyncio.run(run())
