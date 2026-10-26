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

"""Persisted source-dependency lifecycle for Memory and Topic Memory."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy import insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.artifacts.memory import MemoryEntryState
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.tables import SOURCE_LIFECYCLE_NODES_TABLE, TOPIC_MEMORY_ACTIVE_TOPICS_TABLE
from powercontext.builtin.runtime.models import GetTopicMemoryRequest
from powercontext.sources import SourceRef

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import BuiltinRuntime


class SourceLifecycleError(ValueError):
    """Reject an invalid source dependency or lifecycle transition."""


class SourceLifecycleEvent(BaseModel):
    """One versioned Source availability transition."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    node_id: StrictStr
    revision: StrictInt
    sequence: StrictInt
    available: StrictBool
    reason: StrictStr

    @field_validator("node_id", "reason")
    @classmethod
    def require_nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")  # noqa: TRY003
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def require_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("must be positive")  # noqa: TRY003
        return value


_NodeKind: TypeAlias = Literal["source", "memory", "topic"]
_NodeReference: TypeAlias = SourceRef | MemoryCitation | ArtifactRef


class _Node:
    __slots__ = (
        "available",
        "kind",
        "last_event",
        "last_sequence",
        "node_id",
        "parents",
        "policy",
        "reference",
        "scope_id",
        "source_revision",
    )

    def __init__(
        self,
        *,
        scope_id: str,
        node_id: str,
        kind: _NodeKind,
        reference: _NodeReference,
        parents: tuple[str, ...],
        policy: Literal["all", "any"] | None,
        available: bool,
        source_revision: int | None,
        last_sequence: int | None,
        last_event: SourceLifecycleEvent | None,
    ) -> None:
        self.scope_id = scope_id
        self.node_id = node_id
        self.kind = kind
        self.reference = reference
        self.parents = parents
        self.policy = policy
        self.available = available
        self.source_revision = source_revision
        self.last_sequence = last_sequence
        self.last_event = last_event


class SourceLifecycleStore:
    """Small relational store for one scope's immutable dependency graph."""

    def __init__(self, database: AsyncDatabase) -> None:
        self._database = database

    async def get(self, scope_id: str, node_id: str) -> _Node | None:
        async with self._database.connection() as connection:
            row = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES_TABLE).where(
                            SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                            SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
        return None if row is None else _decode_node(row)

    async def nodes(self, scope_id: str) -> tuple[_Node, ...]:
        async with self._database.connection() as connection:
            rows = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_NODES_TABLE)
                    .where(SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id)
                    .order_by(SOURCE_LIFECYCLE_NODES_TABLE.c.node_id)
                )
            ).mappings()
            return tuple(_decode_node(row) for row in rows)

    async def register(
        self,
        node: _Node,
    ) -> None:
        event_payload = None if node.last_event is None else node.last_event.model_dump_json().encode("utf-8")
        reference_payload = node.reference.model_dump_json().encode("utf-8")
        parents_payload = json.dumps(node.parents, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        async with self._database.transaction() as connection:
            await connection.execute(
                insert(SOURCE_LIFECYCLE_NODES_TABLE).values(
                    scope_id=node.scope_id,
                    node_id=node.node_id,
                    kind=node.kind,
                    reference_payload=reference_payload,
                    parents_payload=parents_payload,
                    policy=node.policy,
                    available=node.available,
                    source_revision=node.source_revision,
                    artifact_id=(
                        node.reference.memory_ref.artifact_id
                        if isinstance(node.reference, MemoryCitation)
                        else node.reference.artifact_id
                        if isinstance(node.reference, ArtifactRef)
                        else None
                    ),
                    artifact_revision=(
                        node.reference.memory_ref.revision
                        if isinstance(node.reference, MemoryCitation)
                        else node.reference.revision
                        if isinstance(node.reference, ArtifactRef)
                        else None
                    ),
                    entry_id=node.reference.entry_id if isinstance(node.reference, MemoryCitation) else None,
                    last_sequence=node.last_sequence,
                    last_event_payload=event_payload,
                )
            )
            if node.kind == "topic":
                await connection.execute(
                    update(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE)
                    .where(
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id == node.scope_id,
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id == cast(ArtifactRef, node.reference).artifact_id,
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision == cast(ArtifactRef, node.reference).revision,
                    )
                    .values(lifecycle_available=node.available)
                )

    async def set_available(self, scope_id: str, node_id: str, available: bool) -> None:
        node = await self.get(scope_id, node_id)
        async with self._database.transaction() as connection:
            await connection.execute(
                update(SOURCE_LIFECYCLE_NODES_TABLE)
                .where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node_id,
                )
                .values(available=available)
            )
            if node is not None and node.kind == "topic":
                await connection.execute(
                    update(TOPIC_MEMORY_ACTIVE_TOPICS_TABLE)
                    .where(
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.scope_id == scope_id,
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.artifact_id == cast(ArtifactRef, node.reference).artifact_id,
                        TOPIC_MEMORY_ACTIVE_TOPICS_TABLE.c.revision == cast(ArtifactRef, node.reference).revision,
                    )
                    .values(lifecycle_available=available)
                )

    async def save_event(self, node: _Node, event: SourceLifecycleEvent) -> None:
        async with self._database.transaction() as connection:
            await connection.execute(
                update(SOURCE_LIFECYCLE_NODES_TABLE)
                .where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == node.scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.node_id == node.node_id,
                )
                .values(
                    available=event.available,
                    last_sequence=event.sequence,
                    last_event_payload=event.model_dump_json().encode("utf-8"),
                )
            )

    async def unavailable_topic_ids(self, scope_id: str) -> frozenset[str]:
        async with self._database.connection() as connection:
            values = await connection.scalars(
                select(SOURCE_LIFECYCLE_NODES_TABLE.c.artifact_id).where(
                    SOURCE_LIFECYCLE_NODES_TABLE.c.scope_id == scope_id,
                    SOURCE_LIFECYCLE_NODES_TABLE.c.kind == "topic",
                    SOURCE_LIFECYCLE_NODES_TABLE.c.available.is_(False),
                )
            )
        return frozenset(str(value) for value in values if value is not None)


class SourceLifecycleApplication:
    """Select one scope's lifecycle coordinator."""

    def __init__(self, runtime: BuiltinRuntime, store: SourceLifecycleStore) -> None:
        self._runtime = runtime
        self._store = store

    def for_scope(self, scope_id: str, /) -> ScopedSourceLifecycleService:
        from powercontext.builtin.sources import validate_scope_id

        return ScopedSourceLifecycleService(self._runtime, self._store, validate_scope_id(scope_id))


class ScopedSourceLifecycleService:
    """Register source evidence and propagate explicit availability changes."""

    def __init__(self, runtime: BuiltinRuntime, store: SourceLifecycleStore, scope_id: str) -> None:
        self._runtime = runtime
        self._store = store
        self.scope_id = scope_id

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        _validate_node_id(node_id)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise SourceLifecycleError("source revision must be a positive integer")  # noqa: TRY003
        if not isinstance(source_ref, SourceRef):
            raise SourceLifecycleError("source_ref must be a SourceRef")  # noqa: TRY003
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            sources = await context.sources.catalog.list()
            source = next((value for value in sources if context.sources.catalog.as_ref(value) == source_ref), None)
            if source is None:
                raise SourceLifecycleError("source reference does not exist in this scope")  # noqa: TRY003
            try:
                await context.sources.catalog.read(source)
            except Exception as error:
                raise SourceLifecycleError("source reference is not readable") from error  # noqa: TRY003
            await self._register(
                _Node(
                    scope_id=self.scope_id,
                    node_id=node_id,
                    kind="source",
                    reference=source_ref,
                    parents=(),
                    policy=None,
                    available=True,
                    source_revision=revision,
                    last_sequence=None,
                    last_event=None,
                )
            )

    async def register_memory(
        self,
        node_id: str,
        citation: MemoryCitation,
        *,
        parents: Sequence[str],
        policy: Literal["all", "any"],
    ) -> None:
        _validate_node_id(node_id)
        _validate_derived_arguments(parents, policy)
        if not isinstance(citation, MemoryCitation) or citation.memory_ref.family != "memory":
            raise SourceLifecycleError("citation must address a readable Memory entry")  # noqa: TRY003
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            try:
                await context.artifacts.memory.validate_citation(citation)
                latest = await context.artifacts.memory.head(citation.memory_ref.artifact_id)
                current_entry = next(
                    (
                        item
                        for item in await context.artifacts.memory.entries(latest)
                        if item.entry_id == citation.entry_id
                    ),
                    None,
                )
            except Exception as error:
                raise SourceLifecycleError("Memory citation does not exist or is not readable") from error  # noqa: TRY003
            if current_entry is None or current_entry.entry_version_id != citation.entry_version_id:
                raise SourceLifecycleError("Memory citation is not the current version of its entry")  # noqa: TRY003
            parent_nodes = await self._validate_parents(parents)
            available = _supported(parent_nodes, policy)
            await self._register(
                _Node(
                    scope_id=self.scope_id,
                    node_id=node_id,
                    kind="memory",
                    reference=citation,
                    parents=tuple(parents),
                    policy=policy,
                    available=available,
                    source_revision=None,
                    last_sequence=None,
                    last_event=None,
                )
            )
            await self._reconcile(context, await self._store.nodes(self.scope_id))

    async def register_topic(
        self,
        node_id: str,
        artifact_ref: ArtifactRef,
        *,
        parents: Sequence[str],
        policy: Literal["all", "any"],
    ) -> None:
        _validate_node_id(node_id)
        _validate_derived_arguments(parents, policy)
        if not isinstance(artifact_ref, ArtifactRef) or artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("artifact_ref must address a readable Topic Memory")  # noqa: TRY003
        async with self._runtime._context(self.scope_id), self._runtime._locked(self.scope_id):
            try:
                published = await self._runtime.topic_memory.for_scope(self.scope_id).get(
                    GetTopicMemoryRequest(artifact=artifact_ref)
                )
            except Exception as error:
                raise SourceLifecycleError("Topic Memory reference does not exist or is not readable") from error  # noqa: TRY003
            if published.current_artifact != artifact_ref:
                raise SourceLifecycleError("Topic Memory reference must be its current revision")  # noqa: TRY003
            parent_nodes = await self._validate_parents(parents)
            await self._register(
                _Node(
                    scope_id=self.scope_id,
                    node_id=node_id,
                    kind="topic",
                    reference=artifact_ref,
                    parents=tuple(parents),
                    policy=policy,
                    available=_supported(parent_nodes, policy),
                    source_revision=None,
                    last_sequence=None,
                    last_event=None,
                )
            )

    async def apply(self, event: SourceLifecycleEvent, /) -> list[str]:
        if not isinstance(event, SourceLifecycleEvent):
            raise SourceLifecycleError("event must be a SourceLifecycleEvent")  # noqa: TRY003
        async with self._runtime._context(self.scope_id) as context, self._runtime._locked(self.scope_id):
            source = await self._store.get(self.scope_id, event.node_id)
            if source is None or source.kind != "source":
                raise SourceLifecycleError("lifecycle events must target a registered Source")  # noqa: TRY003
            if event.revision != source.source_revision:
                raise SourceLifecycleError("event revision does not match the registered Source revision")  # noqa: TRY003
            if source.last_sequence is not None:
                if event.sequence < source.last_sequence:
                    raise SourceLifecycleError("event sequence moved backwards")  # noqa: TRY003
                if event.sequence == source.last_sequence:
                    if source.last_event != event:
                        raise SourceLifecycleError("same event sequence has different content")  # noqa: TRY003
                else:
                    await self._store.save_event(source, event)
            else:
                await self._store.save_event(source, event)
            await self._reconcile(context, await self._store.nodes(self.scope_id))
            nodes = await self._store.nodes(self.scope_id)
            return sorted(node.node_id for node in nodes if node.available)

    async def resolve(self, node_id: str, *, purpose: Literal["generation", "audit"]) -> _NodeReference:
        if purpose not in {"generation", "audit"}:
            raise SourceLifecycleError("unknown reference purpose")  # noqa: TRY003
        async with self._runtime._context(self.scope_id):
            node = await self._store.get(self.scope_id, node_id)
        if node is None:
            raise SourceLifecycleError("unknown lifecycle node")  # noqa: TRY003
        if purpose == "generation" and not node.available:
            raise SourceLifecycleError("lifecycle node is unavailable for generation")  # noqa: TRY003
        return node.reference

    async def _register(self, proposed: _Node) -> None:
        existing = await self._store.get(self.scope_id, proposed.node_id)
        if existing is not None:
            if _same_registration(existing, proposed):
                return
            raise SourceLifecycleError("node id is already bound differently in this scope")  # noqa: TRY003
        await self._store.register(proposed)

    async def _validate_parents(self, parents: Sequence[str]) -> tuple[_Node, ...]:
        parent_nodes: list[_Node] = []
        seen: set[str] = set()
        for parent in parents:
            _validate_node_id(parent)
            if parent in seen:
                raise SourceLifecycleError("duplicate parent node")  # noqa: TRY003
            if parent == "":
                raise SourceLifecycleError("self dependency is not allowed")  # noqa: TRY003
            seen.add(parent)
            node = await self._store.get(self.scope_id, parent)
            if node is None:
                raise SourceLifecycleError("all parents must already be registered in this scope")  # noqa: TRY003
            parent_nodes.append(node)
        return tuple(parent_nodes)

    async def _reconcile(self, context: object, nodes: tuple[_Node, ...]) -> None:
        ordered = _topological_order(nodes)
        by_id = {node.node_id: node for node in nodes}
        effective: dict[str, bool] = {node.node_id: node.available for node in nodes if node.kind == "source"}
        for node in ordered:
            if node.kind == "source":
                continue
            parent_available = tuple(effective[parent] for parent in node.parents)
            desired = all(parent_available) if node.policy == "all" else any(parent_available)
            effective[node.node_id] = desired
            if node.kind == "memory":
                await _set_memory_availability(context, cast(MemoryCitation, node.reference), desired)
            if node.available != desired:
                await self._store.set_available(self.scope_id, node.node_id, desired)
                node.available = desired
        # Use the computed values as the authoritative output even if a node store
        # write was interrupted after its Memory transition committed.
        for node_id, available in effective.items():
            node = by_id[node_id]
            node.available = available


async def _set_memory_availability(context: Any, citation: MemoryCitation, available: bool) -> None:
    artifacts = context.artifacts
    service = artifacts.memory
    try:
        current = await service.head(citation.memory_ref.artifact_id)
        entries = await service.entries(current)
    except Exception as error:
        raise SourceLifecycleError("registered Memory entry can no longer be read") from error  # noqa: TRY003
    entry = next((item for item in entries if item.entry_id == citation.entry_id), None)
    if entry is None:
        raise SourceLifecycleError("registered Memory entry can no longer be read")  # noqa: TRY003
    if entry.entry_version_id != citation.entry_version_id:
        raise SourceLifecycleError("registered Memory entry changed after lifecycle binding")  # noqa: TRY003
    desired_state: MemoryEntryState = "active" if available else "inactive"
    manifest = {item.entry_id: item for item in current.content.manifest.entries}
    current_state = manifest.get(entry.entry_id)
    if current_state is None or current_state.state == desired_state:
        return
    if available:
        await service.reactivate(current, entries=(entry,), reason="source lifecycle restored support")
    else:
        await service.forget(current, entries=(entry,), reason="source lifecycle withdrew support")


def _decode_node(row: Any) -> _Node:
    values = row
    kind = cast(_NodeKind, str(values["kind"]))
    reference_payload = cast(bytes, values["reference_payload"])
    if kind == "source":
        reference: _NodeReference = SourceRef.model_validate_json(reference_payload, strict=True)
    elif kind == "memory":
        reference = MemoryCitation.model_validate_json(reference_payload, strict=True)
    else:
        reference = ArtifactRef.model_validate_json(reference_payload, strict=True)
    parents_value = json.loads(cast(bytes, values["parents_payload"]))
    if not isinstance(parents_value, list) or not all(isinstance(item, str) for item in parents_value):
        raise SourceLifecycleError("stored lifecycle parents are corrupt")  # noqa: TRY003
    event_payload = values["last_event_payload"]
    last_event = (
        None
        if event_payload is None
        else SourceLifecycleEvent.model_validate_json(cast(bytes, event_payload), strict=True)
    )
    return _Node(
        scope_id=str(values["scope_id"]),
        node_id=str(values["node_id"]),
        kind=kind,
        reference=reference,
        parents=tuple(parents_value),
        policy=None if values["policy"] is None else cast(Literal["all", "any"], str(values["policy"])),
        available=bool(values["available"]),
        source_revision=None if values["source_revision"] is None else cast(int, values["source_revision"]),
        last_sequence=None if values["last_sequence"] is None else cast(int, values["last_sequence"]),
        last_event=last_event,
    )


def _same_registration(left: _Node, right: _Node) -> bool:
    return (
        left.kind == right.kind
        and left.reference == right.reference
        and left.parents == right.parents
        and left.policy == right.policy
        and left.source_revision == right.source_revision
    )


def _validate_node_id(node_id: str) -> None:
    if not isinstance(node_id, str) or not node_id.strip():
        raise SourceLifecycleError("node id must be a non-blank string")  # noqa: TRY003
    if len(node_id) > 512:
        raise SourceLifecycleError("node id is too long")  # noqa: TRY003


def _validate_derived_arguments(parents: Sequence[str], policy: Literal["all", "any"]) -> None:
    if not isinstance(parents, Sequence) or isinstance(parents, str) or not parents:
        raise SourceLifecycleError("derived nodes require at least one parent")  # noqa: TRY003
    if policy not in {"all", "any"}:
        raise SourceLifecycleError("policy must be all or any")  # noqa: TRY003


def _supported(parents: tuple[_Node, ...], policy: Literal["all", "any"]) -> bool:
    states = tuple(parent.available for parent in parents)
    return all(states) if policy == "all" else any(states)


def _topological_order(nodes: tuple[_Node, ...]) -> tuple[_Node, ...]:
    remaining = {node.node_id: node for node in nodes}
    ordered: list[_Node] = []
    completed: set[str] = set()
    while remaining:
        ready = sorted(
            (node for node in remaining.values() if all(parent in completed for parent in node.parents)),
            key=lambda node: node.node_id,
        )
        if not ready:
            raise SourceLifecycleError("dependency graph contains a cycle")  # noqa: TRY003
        for node in ready:
            ordered.append(node)
            completed.add(node.node_id)
            del remaining[node.node_id]
    return tuple(ordered)
