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

"""seekdb Memory indexes with one-statement hybrid candidate retrieval."""

from __future__ import annotations

from pyobvector import VECTOR
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import TextClause
from typing_extensions import override

from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemoryChannelHit,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex, memory_channel_hits
from powercontext.builtin.persistence.oceanbase.memory_index import (
    OceanBaseMemoryFTSIndex,
    OceanBaseMemoryVectorIndex,
)
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql

_HYBRID_SEARCH_SQL = """
WITH fts_candidates AS (
    SELECT
        f.memory_artifact_id,
        f.head_revision,
        f.entry_id,
        f.entry_version_id,
        v.text,
        MATCH(f.searchable_text) AGAINST (:analyzed_query) AS fts_score,
        CAST(NULL AS DOUBLE) AS distance,
        'fts' AS channel
    FROM pc_memory_entry_heads AS f
    JOIN pc_memory_entry_versions AS v
      ON v.scope_id = f.scope_id
     AND v.memory_artifact_id = f.memory_artifact_id
     AND v.entry_version_id = f.entry_version_id
    WHERE f.scope_id = :scope_id
      AND f.memory_artifact_id IN :memory_artifact_ids
      AND MATCH(f.searchable_text) AGAINST (:analyzed_query)
      {fts_tag_filter}
    ORDER BY
        MATCH(f.searchable_text) AGAINST (:analyzed_query) DESC,
        f.memory_artifact_id,
        f.entry_id,
        f.entry_version_id
    LIMIT :candidate_limit
), vector_neighbors AS (
    SELECT
        m.memory_artifact_id,
        m.head_revision,
        m.entry_id,
        m.entry_version_id,
        m.scope_id,
        l2_distance(m.embedding, :query_vector) AS distance
    FROM pc_memory_vector_entries AS m
    WHERE m.scope_id = :scope_id
      AND m.memory_artifact_id IN :memory_artifact_ids
      {vector_tag_filter}
    ORDER BY l2_distance(m.embedding, :query_vector) APPROXIMATE
    LIMIT :candidate_limit
), vector_candidates AS (
    SELECT
        n.memory_artifact_id,
        n.head_revision,
        n.entry_id,
        n.entry_version_id,
        v.text,
        CAST(NULL AS DOUBLE) AS fts_score,
        n.distance,
        'vector' AS channel
    FROM vector_neighbors AS n
    JOIN pc_memory_entry_versions AS v
      ON v.scope_id = n.scope_id
     AND v.memory_artifact_id = n.memory_artifact_id
     AND v.entry_version_id = n.entry_version_id
)
SELECT *
FROM (
    SELECT * FROM fts_candidates
    UNION ALL
    SELECT * FROM vector_candidates
) AS candidates
ORDER BY
    channel,
    CASE WHEN channel = 'fts' THEN fts_score ELSE -distance END DESC,
    memory_artifact_id,
    entry_id,
    entry_version_id
"""


class SeekDBMemoryIndex(CompositeMemoryIndex):
    """Use the shared native FTS/vector projections and fuse hybrid candidates in one SQL."""

    def __init__(self, profile: EmbeddingProfile | None) -> None:
        fts = OceanBaseMemoryFTSIndex()
        self._vector_index = None if profile is None else OceanBaseMemoryVectorIndex(profile)
        indexes = (fts,) if self._vector_index is None else (fts, self._vector_index)
        super().__init__(*indexes)
        self._hybrid_sql = None if profile is None else _hybrid_search_sql(profile.dimension)
        self._hybrid_sql_no_tags = None if profile is None else _hybrid_search_sql(profile.dimension, tagged=False)

    @override
    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.mode != "hybrid":
            return await super().search(connection, scope_id, request)
        if self._vector_index is None or self._hybrid_sql is None:
            raise CapabilityNotSupportedError("hybrid")
        if request.embedding_profile != self._vector_index.profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        if not await self._vector_index.vector_complete(
            connection,
            scope_id,
            request.memories,
            self._vector_index.profile,
        ):
            raise CapabilityNotSupportedError("vector")

        statement = self._hybrid_sql if request.tag_filter is not None else self._hybrid_sql_no_tags
        if statement is None:
            raise CapabilityNotSupportedError("hybrid")
        rows = (
            await connection.execute(
                statement,
                {
                    "scope_id": scope_id,
                    "memory_artifact_ids": tuple(memory.artifact_id for memory in request.memories),
                    "analyzed_query": request.analyzed_query,
                    "query_vector": validate_embedding(
                        request.query_vector,
                        dimension=self._vector_index.profile.dimension,
                    ),
                    "candidate_limit": request.candidate_limit,
                    **memory_tag_parameters(request.tag_filter),
                },
            )
        ).mappings()
        fts_rows: list[MemoryChannelHit] = []
        vector_rows: list[MemoryChannelHit] = []
        for row in rows:
            channel = str(row["channel"])
            hit = memory_channel_hits((row,), request.memories)
            if not hit:
                continue
            if channel == "fts":
                fts_rows.extend(hit)
            elif channel == "vector":
                vector_rows.extend(hit)
        return MemorySearchChannels(fts=tuple(fts_rows), vector=tuple(vector_rows))


def _hybrid_search_sql(dimension: int, *, tagged: bool = True) -> TextClause:
    sql = _HYBRID_SEARCH_SQL.format(
        fts_tag_filter=memory_tag_sql("f") if tagged else "",
        vector_tag_filter=memory_tag_sql("m") if tagged else "",
    )
    statement = text(sql).bindparams(
        bindparam("memory_artifact_ids", expanding=True),
        bindparam("query_vector", type_=VECTOR(dimension)),
    )
    if tagged:
        statement = statement.bindparams(bindparam("tag_keys", expanding=True), bindparam("tag_hashes", expanding=True))
    return statement


__all__ = ["SeekDBMemoryIndex"]
