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

"""Captured Pi session events and a restartable local-history Connector."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
_CHECKPOINT_VERSION = 1


class PiSessionInput(BaseModel):
    """UTF-8 Pi v3 JSONL session bytes and an optional selected branch leaf."""

    model_config = ConfigDict(frozen=True)

    content: bytes
    leaf_id: str | None = None

    @field_validator("content", mode="before")
    @classmethod
    def validate_content_type(cls, value: object) -> object:
        if not isinstance(value, bytes):
            raise ValueError("content must be bytes")  # noqa: TRY003, TRY004
        return value

    @field_validator("leaf_id")
    @classmethod
    def validate_leaf_id(cls, value: str | None) -> str | None:
        if value is not None and (not value or not value.strip()):
            raise ValueError("leaf_id must be a non-empty string when provided")  # noqa: TRY003
        return value


class PiEventInput(BaseModel):
    """One visible Pi event with its complete, unmodified JSON record."""

    model_config = ConfigDict(frozen=True)

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_event(self) -> PiEventInput:
        for field_name in ("session_id", "event_id", "event_type", "role"):
            value = getattr(self, field_name)
            if not value or not value.strip():
                raise ValueError(f"{field_name} must be a non-empty string")  # noqa: TRY003
        return self


class PiEventSource(Source):
    """Immutable captured event Source, including the full original record."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_identity(self) -> PiEventSource:
        expected = pi_event_source_id(self.session_id, self.event_id)
        if self.source_id != expected or self.name != expected:
            raise ValueError("name and source_id must match the event identity")  # noqa: TRY003
        if self.materialization is not SourceMaterialization.CAPTURED:
            raise ValueError("Pi event Sources must be captured")  # noqa: TRY003
        return self


class PiEventTextEvidenceProjection:
    """Expose event text and stable graph identity through TextEvidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        metadata: dict[str, JsonValue] = {
            "session_id": source.session_id,
            "event_id": source.event_id,
            "parent_id": source.parent_id,
            "event_type": source.event_type,
            "role": source.role,
        }
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata=metadata,
        )


class PiEventSourceDefinition:
    """Resolve, read, and project Pi event captures through Source APIs."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    version = "1"
    source_class = PiEventSource
    projections: tuple[SourceProjection[PiEventSource], ...]

    def __init__(self) -> None:
        self.projections = (cast(SourceProjection[PiEventSource], PiEventTextEvidenceProjection()),)

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiSessionConnector:
    """Incrementally ingest one selected Pi branch with durable validation."""

    name = PI_SESSION_CONNECTOR_NAME
    version = "1"
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        raw = self.path.read_bytes()
        archive = _parse_archive(PiSessionInput(content=raw, leaf_id=self.leaf_id))
        previous_sessions = _read_checkpoint(session.checkpoint)
        previous_imports = previous_sessions.get(archive.session_id, {})

        # Validate every imported record against the complete archive before submitting
        # anything. An event imported from an earlier branch may not be on this leaf.
        for event_id, digest in previous_imports.items():
            record = archive.records_by_id.get(event_id)
            if record is None or _record_digest(record) != digest:
                raise ValueError(f"previously imported Pi event {event_id!r} was removed or changed")  # noqa: TRY003

        imports = dict(previous_imports)
        for event in archive.events:
            digest = _record_digest(event.record)
            existing = imports.get(event.event_id)
            if existing is not None:
                if existing != digest:
                    raise ValueError(f"previously imported Pi event {event.event_id!r} was changed")  # noqa: TRY003
                continue
            item_id = pi_event_source_id(event.session_id, event.event_id)
            outcome = await session.submit(item_id, PI_EVENT_SOURCE_NAME, event)
            if outcome.status is ConnectorSubmissionStatus.ACCEPTED:
                imports[event.event_id] = digest

        sessions = {
            session_id: cast(JsonValue, session_imports) for session_id, session_imports in previous_sessions.items()
        }
        sessions[archive.session_id] = cast(JsonValue, imports)
        checkpoint: dict[str, JsonValue] = {"version": _CHECKPOINT_VERSION, "sessions": sessions}
        return ConnectorRunCompletion(status="complete", checkpoint=checkpoint)


class _PiArchive(BaseModel):
    """Validated graph and visible events for one selected Pi leaf."""

    session_id: str
    records_by_id: dict[str, dict[str, JsonValue]]
    events: tuple[PiEventInput, ...]


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Parse and validate a Pi v3 JSONL archive, returning its selected visible path."""

    return _parse_archive(value).events


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Return a stable bounded identity derived only from session and event IDs."""

    if not isinstance(session_id, str) or not session_id or not session_id.strip():
        raise ValueError("session_id must be a non-empty string")  # noqa: TRY003
    if not isinstance(event_id, str) or not event_id or not event_id.strip():
        raise ValueError("event_id must be a non-empty string")  # noqa: TRY003
    identity = json.dumps([session_id, event_id], ensure_ascii=True, separators=(",", ":")).encode("ascii")
    source_id = f"pi:{hashlib.sha256(identity).hexdigest()}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"event source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


def _parse_archive(value: PiSessionInput) -> _PiArchive:
    decoded = _decode_records(value.content)
    session_id, nodes, parents = _validate_archive_graph(decoded)
    if not nodes:
        raise ValueError("Pi session does not contain a selectable event leaf")  # noqa: TRY003
    leaf_id = value.leaf_id if value.leaf_id is not None else next(reversed(nodes))
    path = _selected_path(leaf_id, nodes, parents)
    events = tuple(event for record in path if (event := _visible_event(session_id, record)) is not None)
    return _PiArchive(session_id=session_id, records_by_id=nodes, events=events)


def _decode_records(content: bytes) -> list[dict[str, JsonValue]]:
    try:
        text = content.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("Pi session must be valid UTF-8 JSONL") from error  # noqa: TRY003

    decoded: list[dict[str, JsonValue]] = []
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            item = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError("Pi session must contain valid JSON records") from error  # noqa: TRY003
        if not isinstance(item, dict):
            raise ValueError("every Pi session record must be a JSON object")  # noqa: TRY003, TRY004
        record = cast(dict[str, JsonValue], item)
        _record_digest(record)
        decoded.append(record)
    return decoded


def _validate_archive_graph(
    decoded: list[dict[str, JsonValue]],
) -> tuple[str, dict[str, dict[str, JsonValue]], dict[str, str | None]]:
    if not decoded:
        raise ValueError("Pi session must contain a v3 session header")  # noqa: TRY003
    header = decoded[0]
    session_id = _validate_header(header)

    nodes: dict[str, dict[str, JsonValue]] = {}
    parents: dict[str, str | None] = {}
    for record in decoded[1:]:
        event_id, parent_id = _validate_event_link(record, nodes)
        nodes[event_id] = record
        parents[event_id] = parent_id
    return session_id, nodes, parents


def _validate_header(header: dict[str, JsonValue]) -> str:
    if header.get("type") != "session" or type(header.get("version")) is not int or header.get("version") != 3:
        raise ValueError("first Pi session record must be a v3 session header")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id or not session_id.strip():
        raise ValueError("Pi session header id must be a non-empty string")  # noqa: TRY003
    return session_id


def _validate_event_link(
    record: dict[str, JsonValue],
    nodes: dict[str, dict[str, JsonValue]],
) -> tuple[str, str | None]:
    event_id = record.get("id")
    if not isinstance(event_id, str) or not event_id or not event_id.strip():
        raise ValueError("Pi event id must be a non-empty string")  # noqa: TRY003
    if event_id in nodes:
        raise ValueError(f"duplicate Pi event id {event_id!r}")  # noqa: TRY003
    if "parentId" not in record:
        raise ValueError(f"Pi event {event_id!r} is missing parentId")  # noqa: TRY003
    parent_id = record["parentId"]
    if parent_id is None:
        return event_id, None
    if not isinstance(parent_id, str) or not parent_id or not parent_id.strip():
        raise ValueError(f"Pi event {event_id!r} has an invalid parentId")  # noqa: TRY003
    if parent_id not in nodes:
        raise ValueError(f"Pi event {event_id!r} has a missing or future parent")  # noqa: TRY003
    return event_id, parent_id


def _selected_path(
    leaf_id: str,
    nodes: dict[str, dict[str, JsonValue]],
    parents: dict[str, str | None],
) -> list[dict[str, JsonValue]]:
    if leaf_id not in nodes:
        raise ValueError(f"selected Pi leaf {leaf_id!r} does not exist")  # noqa: TRY003
    path: list[dict[str, JsonValue]] = []
    visited: set[str] = set()
    current_id: str | None = leaf_id
    while current_id is not None:
        if current_id in visited:
            raise ValueError("Pi session event graph contains a cycle")  # noqa: TRY003
        visited.add(current_id)
        path.append(nodes[current_id])
        current_id = parents[current_id]
    path.reverse()
    return path


def _visible_event(session_id: str, record: dict[str, JsonValue]) -> PiEventInput | None:
    event_id = cast(str, record["id"])
    parent_id = cast(str | None, record["parentId"])
    event_type_value = record.get("type")
    if not isinstance(event_type_value, str) or not event_type_value.strip():
        return None
    event_type = event_type_value
    if event_type == "message":
        visible = _visible_message(record.get("message"))
    elif event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        visible = (event_type, summary) if isinstance(summary, str) and summary else None
    elif event_type == "custom_message":
        body = _visible_text(record.get("content")) or None
        visible = ("custom", body) if body else None
    else:
        visible = None
    if visible is None:
        return None
    role, body = visible
    return PiEventInput(
        session_id=session_id,
        event_id=event_id,
        parent_id=parent_id,
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _visible_message(value: object) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    message = cast(dict[str, object], value)
    raw_role = message.get("role")
    if not isinstance(raw_role, str) or not raw_role:
        return None
    role = raw_role
    if role == "toolResult":
        result_text = _visible_text(message.get("content"))
        if not result_text:
            return None
        tool_name = _display_field(message.get("toolName"))
        call_id = _display_field(message.get("toolCallId"))
        body = f"[tool_result {tool_name} {call_id}] {result_text}"
    elif role == "bashExecution":
        command = _display_field(message.get("command"))
        output = _display_field(message.get("output"))
        if not command and not output:
            return None
        body = f"$ {command}\n{output}"
    else:
        body = _visible_message_body(message.get("content"))
        if not body:
            return None
    return role, body


def _visible_message_body(value: object) -> str | None:
    if isinstance(value, str):
        return value if value else None
    if not isinstance(value, list):
        return None
    chunks: list[str] = []
    for block_value in value:
        if not isinstance(block_value, dict):
            continue
        block = cast(dict[str, object], block_value)
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            text = cast(str, block["text"])
            if text:
                chunks.append(text)
        elif block_type == "toolCall":
            tool_name = _display_field(block.get("name"))
            call_id = _display_field(block.get("id"))
            arguments = block.get("arguments", {})
            try:
                encoded_arguments = json.dumps(
                    arguments,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
            except (TypeError, ValueError) as error:
                raise ValueError("Pi tool-call arguments must be valid JSON") from error  # noqa: TRY003
            chunks.append(f"[tool_call {tool_name} {call_id}] {encoded_arguments}")
    return "\n".join(chunks) if chunks else None


def _visible_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    chunks: list[str] = []
    for block_value in value:
        if isinstance(block_value, dict):
            block = cast(dict[str, object], block_value)
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = cast(str, block["text"])
                if text:
                    chunks.append(text)
    return "\n".join(chunks)


def _display_field(value: object) -> str:
    return value if isinstance(value, str) else ""


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")  # noqa: TRY003
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant {value}")  # noqa: TRY003


def _record_digest(record: dict[str, JsonValue]) -> str:
    try:
        canonical = json.dumps(
            record,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8", errors="strict")
    except (UnicodeEncodeError, TypeError, ValueError) as error:
        raise ValueError("Pi record cannot be normalized as strict JSON") from error  # noqa: TRY003
    return hashlib.sha256(canonical).hexdigest()


def _read_checkpoint(checkpoint: JsonValue | None) -> dict[str, dict[str, str]]:
    if checkpoint is None:
        return {}
    if not isinstance(checkpoint, dict):
        raise ValueError("Pi connector checkpoint must be an object")  # noqa: TRY003, TRY004
    if type(checkpoint.get("version")) is not int or checkpoint.get("version") != _CHECKPOINT_VERSION:
        raise ValueError("Pi connector checkpoint has an unsupported version")  # noqa: TRY003
    sessions = checkpoint.get("sessions")
    if not isinstance(sessions, dict):
        raise ValueError("Pi connector checkpoint is missing sessions")  # noqa: TRY003, TRY004
    result: dict[str, dict[str, str]] = {}
    for session_id, imported_value in sessions.items():
        if not isinstance(session_id, str) or not session_id or not session_id.strip():
            raise ValueError("Pi connector checkpoint contains an invalid session id")  # noqa: TRY003
        if not isinstance(imported_value, dict):
            raise ValueError("Pi connector checkpoint contains an invalid session record")  # noqa: TRY003, TRY004
        imported: dict[str, str] = {}
        for event_id, digest in imported_value.items():
            if (
                not isinstance(event_id, str)
                or not event_id
                or not event_id.strip()
                or not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError("Pi connector checkpoint contains an invalid event record")  # noqa: TRY003
            imported[event_id] = digest
        result[session_id] = imported
    return result


PI_EVENT_SOURCE_DEFINITION = cast(
    SourceDefinition[PiEventInput, PiEventSource, PiEventInput],
    PiEventSourceDefinition(),
)


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiEventSourceDefinition",
    "PiEventTextEvidenceProjection",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
