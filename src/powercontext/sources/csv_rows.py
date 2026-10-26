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

"""Captured row-level Sources for offline CSV documents."""

from __future__ import annotations

import csv
import io
import json
from typing import cast
from urllib.parse import quote

from pydantic import BaseModel, JsonValue, field_validator, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, SourceMaterialization, TextEvidence
from powercontext.sources.definitions import SourceDefinition, SourceProjection
from powercontext.sources.models import Source

CSV_ROW_SOURCE_NAME = "csv-row"


class CSVDocumentInput(BaseModel):
    """Complete offline CSV bytes and the document's stable row-key contract."""

    document_id: str
    primary_key: str
    content: bytes

    @field_validator("document_id")
    @classmethod
    def validate_document_id(cls, value: str) -> str:
        _validate_document_id(value)
        return value

    @field_validator("primary_key")
    @classmethod
    def validate_primary_key(cls, value: str) -> str:
        if not value:
            raise ValueError("primary_key must be non-empty")  # noqa: TRY003
        return value

    @field_validator("content", mode="before")
    @classmethod
    def validate_content_type(cls, value: object) -> object:
        if not isinstance(value, bytes):
            raise ValueError("content must be bytes")  # noqa: TRY003, TRY004
        return value


class CSVRowInput(BaseModel):
    """One validated CSV row in its original header and field order."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_row(self) -> CSVRowInput:
        _validate_document_id(self.document_id)
        _validate_primary_key(self.primary_key)
        _validate_columns(self.columns)
        if self.primary_key not in self.columns:
            raise ValueError("primary_key must exactly match a column")  # noqa: TRY003
        if len(self.columns) != len(self.values):
            raise ValueError("row values must match the header width")  # noqa: TRY003
        _validate_primary_key_value(self.primary_key_value)
        if self.values[self.columns.index(self.primary_key)] != self.primary_key_value:
            raise ValueError("primary_key_value must match the row's primary-key field")  # noqa: TRY003
        csv_row_source_id(self.document_id, self.primary_key_value)
        return self


class CSVRowSource(Source):
    """A captured CSV row whose stable identity is document plus primary key."""

    materialization: SourceMaterialization = SourceMaterialization.CAPTURED
    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @field_validator("materialization")
    @classmethod
    def require_captured_materialization(cls, value: SourceMaterialization) -> SourceMaterialization:
        if value is not SourceMaterialization.CAPTURED:
            raise ValueError("CSV row Sources must be captured")  # noqa: TRY003
        return value

    @model_validator(mode="after")
    def validate_row_source(self) -> CSVRowSource:
        _validate_document_id(self.document_id)
        _validate_primary_key(self.primary_key)
        _validate_columns(self.columns)
        if self.primary_key not in self.columns:
            raise ValueError("primary_key must exactly match a column")  # noqa: TRY003
        if len(self.columns) != len(self.values):
            raise ValueError("row values must match the header width")  # noqa: TRY003
        _validate_primary_key_value(self.primary_key_value)
        if self.values[self.columns.index(self.primary_key)] != self.primary_key_value:
            raise ValueError("primary_key_value must match the row's primary-key field")  # noqa: TRY003
        expected_id = csv_row_source_id(self.document_id, self.primary_key_value)
        if self.source_id != expected_id or self.name != expected_id:
            raise ValueError("name and source_id must match the row identity")  # noqa: TRY003
        return self


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse and atomically validate a complete CSV document."""

    if not value.primary_key:
        raise ValueError("primary_key must be non-empty")  # noqa: TRY003

    try:
        text = value.content.decode("utf-8-sig", errors="strict")
        reader = csv.reader(io.StringIO(text, newline=""), strict=True)
        columns = next(reader, None)
        if columns is None:
            raise ValueError("CSV document must contain a header")  # noqa: TRY003
        column_tuple = tuple(columns)
        _validate_columns(column_tuple)
        try:
            primary_key_index = column_tuple.index(value.primary_key)
        except ValueError:
            raise ValueError("primary_key must exactly match a column") from None  # noqa: TRY003

        rows: list[CSVRowInput] = []
        primary_key_values: set[str] = set()
        for values in reader:
            if not values:
                continue
            if len(values) != len(column_tuple):
                raise ValueError("CSV row width must match the header")  # noqa: TRY003
            primary_key_value = values[primary_key_index]
            _validate_primary_key_value(primary_key_value)
            if primary_key_value in primary_key_values:
                raise ValueError("primary_key values must be unique within the document")  # noqa: TRY003
            primary_key_values.add(primary_key_value)
            rows.append(
                CSVRowInput(
                    document_id=value.document_id,
                    primary_key=value.primary_key,
                    primary_key_value=primary_key_value,
                    columns=column_tuple,
                    values=tuple(values),
                )
            )
    except (UnicodeDecodeError, csv.Error) as error:
        raise ValueError("CSV document must be valid strict UTF-8 CSV") from error  # noqa: TRY003

    return tuple(rows)


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Return the delimiter-safe, bounded identity for one captured CSV row."""

    _validate_document_id(document_id)
    _validate_primary_key_value(primary_key_value)
    source_id = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"row source identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


class CSVRowTextEvidenceProjection:
    """Expose the complete ordered row through the standard text evidence schema."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row: dict[str, JsonValue] = dict(zip(source.columns, source.values, strict=True))
        content = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        metadata: dict[str, JsonValue] = {
            "document_id": source.document_id,
            "primary_key": source.primary_key,
            "primary_key_value": source.primary_key_value,
            "columns": list(source.columns),
            "row": row,
        }
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=content,
            metadata=metadata,
        )


class CSVRowSourceDefinition:
    """Resolve, read, and project CSV row captures through Source APIs."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    version = "1"
    source_class = CSVRowSource
    projections: tuple[SourceProjection[CSVRowSource], ...]

    def __init__(self) -> None:
        self.projections = (cast(SourceProjection[CSVRowSource], CSVRowTextEvidenceProjection()),)

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            definition_version=self.version,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


def _validate_document_id(value: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("document_id must be non-empty and have no surrounding whitespace")  # noqa: TRY003


def _validate_primary_key(value: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("primary_key must be non-empty")  # noqa: TRY003


def _validate_primary_key_value(value: str) -> None:
    if not isinstance(value, str) or not value or not value.strip():
        raise ValueError("primary_key_value must not be empty or whitespace-only")  # noqa: TRY003


def _validate_columns(columns: tuple[str, ...]) -> None:
    if not isinstance(columns, tuple) or not columns:
        raise ValueError("CSV header must contain at least one column")  # noqa: TRY003
    if any(not isinstance(column, str) or not column or not column.strip() for column in columns):
        raise ValueError("CSV header columns must not be empty or whitespace-only")  # noqa: TRY003
    if len(columns) != len(set(columns)):
        raise ValueError("CSV header columns must be unique")  # noqa: TRY003


CSV_ROW_SOURCE_DEFINITION = cast(
    SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput],
    CSVRowSourceDefinition(),
)


__all__ = [
    "CSV_ROW_SOURCE_DEFINITION",
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSVRowSourceDefinition",
    "CSVRowTextEvidenceProjection",
    "csv_row_source_id",
    "parse_csv_document",
]
