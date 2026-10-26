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

import pytest

from powercontext.builtin.artifacts.memory import MemoryEntryInput, MemoryService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    RememberMemoryRequest,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleEvent
from powercontext.builtin.scope import ScopeDraft


def test_lifecycle_retry_after_memory_transition_failure_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        config = BuiltinConfig(database=SQLiteConfig())
        async with open_builtin_runtime(config) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title="Lifecycle retry", summary="Failure recovery", idempotency_key="retry")
            )
            scope_id = scope.scope_id
            source = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(source_id="source", content="original evidence", metadata={})
            )
            entry = await runtime.memory.for_scope(scope_id).remember(
                RememberMemoryRequest(
                    entries=(MemoryEntryInput(kind="fact", text="retained evidence for source lifecycle"),)
                )
            )
            lifecycle = runtime.lifecycle.for_scope(scope_id)
            await lifecycle.register_source("source", source.source_ref, revision=1)
            await lifecycle.register_memory(
                "memory", entry.entry.citation, parents=("source",), policy="all"
            )

            original_forget = MemoryService.forget
            failed = False

            async def fail_once(self, *args, **kwargs):
                nonlocal failed
                if not failed:
                    failed = True
                    raise RuntimeError("injected Memory transition failure")  # noqa: TRY003
                return await original_forget(self, *args, **kwargs)

            monkeypatch.setattr(MemoryService, "forget", fail_once)
            withdrawal = SourceLifecycleEvent(
                node_id="source", revision=1, sequence=1, available=False, reason="withdrawn"
            )
            with pytest.raises(RuntimeError, match="injected Memory transition failure"):
                await lifecycle.apply(withdrawal)

            active = await lifecycle.apply(withdrawal)
            assert active == []
            after_withdrawal = await runtime.memory.for_scope(scope_id).list(include_inactive=True)
            assert next(item for item in after_withdrawal.entries if item.entry.text.startswith("retained")).state == "inactive"
            withdrawn_ref = after_withdrawal.memory_ref
            assert withdrawn_ref is not None

            replayed = await lifecycle.apply(withdrawal)
            assert replayed == []
            assert (await runtime.memory.for_scope(scope_id).list(include_inactive=True)).memory_ref == withdrawn_ref
            assert not (
                await runtime.memory.for_scope(scope_id).search(
                    SearchMemoryRequest(query="retained evidence", mode="fts", limit=10)
                )
            ).hits

            restoration = SourceLifecycleEvent(
                node_id="source", revision=1, sequence=2, available=True, reason="restored"
            )
            assert await lifecycle.apply(restoration) == ["memory", "source"]
            assert (
                await runtime.memory.for_scope(scope_id).search(
                    SearchMemoryRequest(query="retained evidence", mode="fts", limit=10)
                )
            ).hits

    asyncio.run(run())
