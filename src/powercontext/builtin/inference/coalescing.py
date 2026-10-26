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

"""Coalesce concurrent embedding work and retain a bounded vector cache."""

from __future__ import annotations

import asyncio
import math
from collections import OrderedDict, deque
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TypeAlias

from powercontext.builtin.inference.models import EmbeddingResult, EmbeddingVector, InferenceUsage
from powercontext.builtin.inference.protocols import EmbeddingModel, QueryEmbeddingModel

_CacheKey: TypeAlias = tuple[str, str]


@dataclass(frozen=True, slots=True)
class _ResolvedVector:
    vector: EmbeddingVector
    usage: InferenceUsage
    batch_id: int


class CoalescingEmbeddingModel:
    """Share concurrent embedding requests and cache successful vectors."""

    def __init__(
        self,
        model: EmbeddingModel,
        *,
        max_batch_size: int = 64,
        coalesce_ms: float = 5.0,
        cache_size: int = 1024,
    ) -> None:
        if isinstance(max_batch_size, bool) or not isinstance(max_batch_size, int) or max_batch_size < 1:
            raise ValueError
        if (
            isinstance(coalesce_ms, bool)
            or not isinstance(coalesce_ms, (int, float))
            or not math.isfinite(coalesce_ms)
            or coalesce_ms < 0
        ):
            raise ValueError
        if isinstance(cache_size, bool) or not isinstance(cache_size, int) or cache_size < 0:
            raise ValueError

        self._model = model
        self.profile = model.profile
        self._max_batch_size = max_batch_size
        self._coalesce_seconds = coalesce_ms / 1000
        self._cache_size = cache_size
        self._lock = asyncio.Lock()
        self._cache: OrderedDict[_CacheKey, EmbeddingVector] = OrderedDict()
        self._pending: dict[_CacheKey, asyncio.Future[_ResolvedVector]] = {}
        self._queued: dict[str, deque[_CacheKey]] = {"document": deque(), "query": deque()}
        self._worker: asyncio.Task[None] | None = None
        self._next_batch_id = 0
        self._query_specific = isinstance(model, QueryEmbeddingModel)

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed documents, restoring original order and duplicate positions."""

        return await self._embed(texts, kind="document")

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Embed query text when the wrapped model distinguishes query vectors."""

        return await self._embed(texts, kind="query" if self._query_specific else "document")

    async def _embed(self, texts: tuple[str, ...], *, kind: str) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(vectors=())

        keys = tuple((kind, text) for text in texts)
        unique_keys = tuple(dict.fromkeys(keys))
        immediate: dict[_CacheKey, EmbeddingVector] = {}
        waiting: dict[_CacheKey, asyncio.Future[_ResolvedVector]] = {}
        loop = asyncio.get_running_loop()

        async with self._lock:
            for key in unique_keys:
                cached = self._cache.get(key)
                if cached is not None:
                    self._cache.move_to_end(key)
                    immediate[key] = cached
                    continue

                future = self._pending.get(key)
                if future is None:
                    future = loop.create_future()
                    future.add_done_callback(_consume_unobserved_exception)
                    self._pending[key] = future
                    self._queued[key[0]].append(key)
                waiting[key] = future

            if any(self._queued.values()) and (self._worker is None or self._worker.done()):
                self._worker = asyncio.create_task(self._drain_queue())

        if waiting:
            outcomes = await asyncio.gather(*(asyncio.shield(future) for future in waiting.values()))
            resolved = dict(zip(waiting, outcomes, strict=True))
        else:
            resolved = {}

        vectors_by_key = dict(immediate)
        vectors_by_key.update({key: value.vector for key, value in resolved.items()})
        batch_usage: dict[int, InferenceUsage] = {}
        for value in resolved.values():
            batch_usage.setdefault(value.batch_id, value.usage)

        return EmbeddingResult(
            vectors=tuple(vectors_by_key[key] for key in keys),
            usage=_combine_usage(batch_usage.values()),
        )

    async def _drain_queue(self) -> None:
        try:
            while True:
                await self._coalesce_window()
                batch = await self._take_batch()
                if batch is None:
                    return
                keys, futures, batch_id = batch
                await self._process_batch(keys, futures, batch_id)
        finally:
            async with self._lock:
                if self._worker is asyncio.current_task():
                    self._worker = None
                    if any(self._queued.values()):
                        self._worker = asyncio.create_task(self._drain_queue())

    async def _coalesce_window(self) -> None:
        # A zero window still yields once so already-runnable calls can join.
        await asyncio.sleep(self._coalesce_seconds)

    async def _take_batch(
        self,
    ) -> tuple[list[_CacheKey], list[asyncio.Future[_ResolvedVector]], int] | None:
        async with self._lock:
            kind = next((name for name, queue in self._queued.items() if queue), None)
            if kind is None:
                self._worker = None
                return None
            queue = self._queued[kind]
            keys = [queue.popleft() for _ in range(min(self._max_batch_size, len(queue)))]
            futures = [self._pending[key] for key in keys]
            batch_id = self._next_batch_id
            self._next_batch_id += 1
            return keys, futures, batch_id

    async def _process_batch(
        self,
        keys: list[_CacheKey],
        futures: list[asyncio.Future[_ResolvedVector]],
        batch_id: int,
    ) -> None:
        try:
            result = await self._run_batch(keys)
            _validate_vector_count(result, len(keys))
        except asyncio.CancelledError:
            await self._fail_batch(keys, futures, cancelled=True)
        except Exception as error:
            await self._fail_batch(keys, futures, error=error)
        except BaseException as error:
            await self._fail_batch(keys, futures, error=error)
            raise
        else:
            await self._complete_batch(keys, futures, result, batch_id)

    async def _run_batch(self, keys: list[_CacheKey]) -> EmbeddingResult:
        kind = keys[0][0]
        texts = tuple(text for _, text in keys)
        if kind == "query" and isinstance(self._model, QueryEmbeddingModel):
            return await self._model.embed_query(texts)
        return await self._model.embed(texts)

    async def _complete_batch(
        self,
        keys: list[_CacheKey],
        futures: list[asyncio.Future[_ResolvedVector]],
        result: EmbeddingResult,
        batch_id: int,
    ) -> None:
        async with self._lock:
            for key, future, vector in zip(keys, futures, result.vectors, strict=True):
                if self._pending.get(key) is future:
                    del self._pending[key]
                if self._cache_size:
                    self._cache[key] = vector
                    self._cache.move_to_end(key)
                    while len(self._cache) > self._cache_size:
                        self._cache.popitem(last=False)
                if not future.done():
                    future.set_result(_ResolvedVector(vector, result.usage, batch_id))

    async def _fail_batch(
        self,
        keys: list[_CacheKey],
        futures: list[asyncio.Future[_ResolvedVector]],
        *,
        error: BaseException | None = None,
        cancelled: bool = False,
    ) -> None:
        async with self._lock:
            for key, future in zip(keys, futures, strict=True):
                if self._pending.get(key) is future:
                    del self._pending[key]
                if not future.done():
                    if cancelled or error is None:
                        future.cancel()
                    else:
                        future.set_exception(error)


def _consume_unobserved_exception(future: asyncio.Future[_ResolvedVector]) -> None:
    """Mark abandoned shared failures as retrieved without changing await behavior."""

    if not future.cancelled():
        future.exception()


def _validate_vector_count(result: EmbeddingResult, expected: int) -> None:
    if len(result.vectors) != expected:
        raise ValueError


def _combine_usage(usages: Iterable[InferenceUsage]) -> InferenceUsage:
    values = tuple(usages)
    if not values:
        return InferenceUsage(requests=0)
    return InferenceUsage(
        requests=sum(usage.requests for usage in values),
        input_tokens=_sum_optional(usage.input_tokens for usage in values),
        output_tokens=_sum_optional(usage.output_tokens for usage in values),
    )


def _sum_optional(values: Iterable[int | None]) -> int | None:
    total = 0
    for value in values:
        if value is None:
            return None
        total += value
    return total


__all__ = ["CoalescingEmbeddingModel"]
