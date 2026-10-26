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

"""HTTP scoring adapter for identity-preserving Memory reranking."""

from __future__ import annotations

import json
import math
from typing import cast

import httpx

from powercontext.builtin.artifacts.memory.models import MemoryHit
from powercontext.builtin.artifacts.memory.reranking import MemoryRerankDecision, _validate_bounds
from powercontext.builtin.inference import InferenceUsage


class HTTPMemoryReranker:
    """Score all coarse candidates over HTTP and select them in one global order."""

    def __init__(
        self,
        endpoint: str,
        *,
        client: httpx.AsyncClient,
        batch_size: int = 32,
        policy_id: str = "http-cross-encoder-v1",
    ) -> None:
        if not isinstance(batch_size, int) or isinstance(batch_size, bool) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")  # noqa: TRY003
        self.endpoint = endpoint
        self.client = client
        self.batch_size = batch_size
        self.policy_id = policy_id

    async def rerank(
        self,
        query: str,
        candidates: tuple[MemoryHit, ...],
        limit: int,
        /,
    ) -> MemoryRerankDecision:
        """Score each globally ranked candidate once, then apply a stable global top-k."""

        try:
            candidate_count = len(candidates)
        except TypeError as error:
            raise ValueError("candidates must be a non-empty sequence") from error  # noqa: TRY003
        if candidate_count < 1:
            raise ValueError("rerank requires at least one candidate")  # noqa: TRY003
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ValueError("limit must be an integer within the candidate count")  # noqa: TRY003, TRY004
        _validate_bounds(candidate_count, limit)

        scores_by_rank: dict[int, int | float] = {}
        request_count = 0
        for start in range(0, candidate_count, self.batch_size):
            batch = candidates[start : start + self.batch_size]
            ranks = tuple(range(start + 1, start + len(batch) + 1))
            response = await self.client.post(
                self.endpoint,
                json={
                    "query": query,
                    "candidates": [
                        {"rank": rank, "text": candidate.text} for rank, candidate in zip(ranks, batch, strict=True)
                    ],
                },
            )
            request_count += 1
            response.raise_for_status()
            try:
                payload = json.loads(response.content, object_pairs_hook=_unique_json_object)
            except (RecursionError, TypeError, ValueError) as error:
                raise ValueError("reranker response must be valid JSON with unique object fields") from error  # noqa: TRY003
            scores_by_rank.update(_parse_batch_scores(payload, ranks))

        selected_ranks = tuple(sorted(scores_by_rank, key=lambda rank: (-scores_by_rank[rank], rank))[:limit])
        return MemoryRerankDecision(
            selected_ranks=selected_ranks,
            usage=InferenceUsage(requests=request_count),
        )


def _parse_batch_scores(payload: object, expected_ranks: tuple[int, ...]) -> dict[int, int | float]:
    if not isinstance(payload, dict) or set(payload) != {"scores"}:
        raise ValueError("reranker response must contain only a scores field")  # noqa: TRY003
    payload_fields = cast(dict[str, object], payload)
    records = payload_fields["scores"]
    if not isinstance(records, list):
        raise ValueError("reranker scores must be a list")  # noqa: TRY003, TRY004

    expected = set(expected_ranks)
    scores: dict[int, int | float] = {}
    for record in records:
        if not isinstance(record, dict) or set(record) != {"rank", "score"}:
            raise ValueError("each reranker score must contain only rank and score")  # noqa: TRY003
        record_fields = cast(dict[str, object], record)
        rank = record_fields["rank"]
        if not isinstance(rank, int) or isinstance(rank, bool) or rank not in expected:
            raise ValueError("reranker returned a rank outside the current batch")  # noqa: TRY003
        if rank in scores:
            raise ValueError("reranker returned a duplicate rank")  # noqa: TRY003

        raw_score = record_fields["score"]
        if not isinstance(raw_score, (int, float)) or isinstance(raw_score, bool):
            raise ValueError("reranker scores must be numeric")  # noqa: TRY003, TRY004
        if isinstance(raw_score, float) and not math.isfinite(raw_score):
            raise ValueError("reranker scores must be finite")  # noqa: TRY003
        scores[rank] = raw_score

    if scores.keys() != expected:
        raise ValueError("reranker response must score every rank in the current batch exactly once")  # noqa: TRY003
    return scores


def _unique_json_object(pairs: list[tuple[str, object]], /) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("reranker response contains a duplicate JSON field")  # noqa: TRY003
        value[key] = item
    return value


__all__ = ["HTTPMemoryReranker"]
