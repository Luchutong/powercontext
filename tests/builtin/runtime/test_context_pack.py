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
import base64
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    PrepareContextRequest,
    RememberMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.context_pack import ContextPack, ContextPackDelta, apply_context_pack_delta
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentSource


def _receive(wire: bytes, base: ContextPack | None) -> ContextPack:
    """Decode the protocol with an independent digest and Base64 implementation."""

    payload = json.loads(wire)
    if base is None:
        assert payload["schema"] == "powercontext.context-pack.v1"
        assert payload["byte_count"] == len(wire)
        items = tuple(base64.b64decode(item, validate=True) for item in payload["items"])
        expected_digest = payload["digest"]
    else:
        assert payload["schema"] == "powercontext.context-pack-delta.v1"
        assert payload["base_digest"] == base.digest
        items = base.items[: payload["prefix_items"]] + tuple(
            base64.b64decode(item, validate=True) for item in payload["appended"]
        )
        expected_digest = payload["target_digest"]
    unsigned = {
        "schema": "powercontext.context-pack.v1",
        "items": [base64.b64encode(item).decode() for item in items],
    }
    digest = hashlib.sha256(json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert expected_digest == "sha256:" + digest
    return ContextPack.from_bytes(ContextPack.build(items).to_bytes())


@pytest.mark.parametrize("chunk_bytes", [17, 128, 512])
@pytest.mark.parametrize("suffix_chars", [0, 23])
def test_incremental_prepare_reuses_serialized_base_boundaries(chunk_bytes: int, suffix_chars: int) -> None:
    async def run() -> None:
        async with open_builtin_runtime(BuiltinConfig()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Pack boundaries", summary="Independent receiver", idempotency_key="boundaries")
            )
            await runtime.memory.for_scope(scope.scope_id).remember(
                RememberMemoryRequest(entries=(MemoryEntryInput(kind="fact", text="remote 控制器 design. " * 40),))
            )
            context = runtime.context.for_scope(scope.scope_id)
            request = PrepareContextRequest(query="remote", max_bytes=4096)
            prepared = await context.prepare(request)
            assert prepared.content is not None
            base_content = prepared.content[:-suffix_chars] if suffix_chars else prepared.content
            base = ContextPack.from_bytes(ContextPack.from_content(base_content, chunk_bytes=chunk_bytes).to_bytes())
            sent = await context.prepare_incremental(request, base_pack=base)
            assert isinstance(sent, ContextPackDelta)

        wire = sent.to_bytes()
        rebuilt = _receive(wire, base)
        assert b"".join(rebuilt.items) == prepared.content.encode()
        assert apply_context_pack_delta(base, ContextPackDelta.from_bytes(wire)) == rebuilt
        assert 1 - len(wire) / prepared.content_bytes >= 0.45

    asyncio.run(run())


def test_incremental_prepare_reconstructs_empty_context_with_a_nonempty_base() -> None:
    async def run() -> None:
        async with open_builtin_runtime(BuiltinConfig()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Empty pack", summary="No evidence", idempotency_key="empty")
            )
            context = runtime.context.for_scope(scope.scope_id)
            request = PrepareContextRequest(query="remote")
            expected = await context.prepare(request)
            assert expected.content is None
            first = await context.prepare_incremental(request)
            assert isinstance(first, ContextPack) and first.items == ()
            base = ContextPack.build((b"", b"previous context"))
            sent = await context.prepare_incremental(request, base_pack=base)
            rebuilt = _receive(sent.to_bytes(), base)
            assert rebuilt.items == ()

    asyncio.run(run())


class _MeetingPipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="conversation-turn", text=source.content, sources=(source,))
            for source in request.sources
            if isinstance(source, ContentSource)
        )


def test_incremental_prepare_meeting_sequence_wire_cost() -> None:
    async def run() -> None:
        fixture = json.loads((Path(__file__).parents[2] / "fixtures/context_pack/qmsum_turns.json").read_text())
        measurement = fixture["measurement"]
        turns = fixture["turns"]
        base = None
        contents = set()
        raw_bytes = wire_bytes = full_bytes = gzip_bytes = 0
        async with open_builtin_runtime(BuiltinConfig(), candidate_pipeline=_MeetingPipeline()) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="QMSum cost", summary="Local 40-round sequence", idempotency_key="cost")
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            memory = runtime.memory.for_scope(scope.scope_id)
            context = runtime.context.for_scope(scope.scope_id)

            async def capture(index: int) -> None:
                turn = turns[index]
                await sources.capture(
                    CaptureSource(source_id=f"turn-{turn['turn_index']}", content=turn["text"], metadata={})
                )

            next_turn = measurement["initial_turns"]
            for index in range(next_turn):
                await capture(index)
            while (await memory.flush()).processed:
                pass

            for index in range(measurement["rounds"]):
                if index and index % measurement["append_every"] == 0:
                    await capture(next_turn)
                    next_turn += 1
                    while (await memory.flush()).processed:
                        pass
                query = measurement["queries"][index // measurement["switch_query_every"]]
                request = PrepareContextRequest(query=query, max_bytes=measurement["max_bytes"])
                expected = await context.prepare(request)
                assert expected.content is not None
                content = expected.content.encode()
                sent = await context.prepare_incremental(request, base_pack=base)
                wire = sent.to_bytes()
                base = _receive(wire, base)
                assert b"".join(base.items) == content
                raw_bytes += len(content)
                wire_bytes += len(wire)
                full_bytes += len(base.to_bytes())
                gzip_bytes += len(gzip.compress(content, compresslevel=9, mtime=0))
                contents.add(content)

        metrics = {
            "rounds": measurement["rounds"],
            "distinct_contexts": len(contents),
            "raw_full_bytes": raw_bytes,
            "incremental_bytes": wire_bytes,
            "full_pack_bytes": full_bytes,
            "gzip_bytes": gzip_bytes,
            "saving_vs_raw": 1 - wire_bytes / raw_bytes,
            "saving_vs_pack": 1 - wire_bytes / full_bytes,
            "saving_vs_gzip": 1 - wire_bytes / gzip_bytes,
        }
        print(json.dumps(metrics, sort_keys=True))
        assert len(contents) >= 4
        assert metrics["saving_vs_raw"] >= 0.45

    asyncio.run(run())
