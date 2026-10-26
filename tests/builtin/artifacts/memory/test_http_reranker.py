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

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory.http_reranker import HTTPMemoryReranker
from powercontext.builtin.artifacts.memory.models import MemoryHit


def _hit(rank: int) -> MemoryHit:
    return MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="memory", revision=1),
        entry_id=f"entry-{rank}",
        entry_version_id=f"entry-{rank}-v1",
        text=f"Candidate {rank}",
        score=1 / rank,
        matched_by=("fts",),
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"scores": [{"rank": True, "score": 0.5}]},
        {"scores": [{"rank": 1, "score": True}]},
        {"scores": [{"rank": 1, "score": "0.5"}]},
    ],
)
def test_rejects_boolean_or_non_numeric_scores(payload: object) -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
        async with httpx.AsyncClient(transport=transport) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_rejects_non_json_and_http_status_errors() -> None:
    async def scenario() -> None:
        malformed = httpx.MockTransport(lambda _request: httpx.Response(200, content=b"not json"))
        async with httpx.AsyncClient(transport=malformed) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://reranker.invalid/score", client=client).rerank("query", (_hit(1),), 1)

        duplicate_fields = httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                content=b'{"scores":[{"rank":1,"rank":1,"score":0.5}]}',
            )
        )
        async with httpx.AsyncClient(transport=duplicate_fields) as client:
            with pytest.raises(ValueError):
                await HTTPMemoryReranker("https://reranker.invalid/score", client=client).rerank("query", (_hit(1),), 1)

        failing = httpx.MockTransport(lambda _request: httpx.Response(503))
        async with httpx.AsyncClient(transport=failing) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await HTTPMemoryReranker("https://reranker.invalid/score", client=client).rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())


def test_rejects_boolean_batch_size() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError):
                HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=True)

    asyncio.run(scenario())


def test_accepts_finite_large_integer_scores() -> None:
    async def scenario() -> None:
        large_score = 10**400
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "scores": [
                        {"rank": 1, "score": large_score},
                        {"rank": 2, "score": large_score - 1},
                    ]
                },
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            reranker = HTTPMemoryReranker("https://reranker.invalid/score", client=client, batch_size=2)
            decision = await reranker.rerank("query", (_hit(1), _hit(2)), 2)
        assert decision.selected_ranks == (1, 2)

    asyncio.run(scenario())
