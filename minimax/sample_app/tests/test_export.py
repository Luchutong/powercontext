"""CSV export behaviour (EXP-001, EXP-002)."""

import pytest

from taskboard.export import export_csv


@pytest.mark.spec("EXP-001")
def test_export_header_and_row_order(tasks):
    lines = export_csv(reversed(tasks)).splitlines()
    assert lines[0] == "id,title,status,priority,tags"
    assert [line.split(",", 1)[0] for line in lines[1:]] == [str(n) for n in range(1, 9)]


@pytest.mark.spec("EXP-002")
def test_export_joins_tags_with_semicolon(client):
    status, body = client.get("/tasks/export.csv")
    assert status == 200
    assert "8,权限校验遗漏,open,medium,backend;security;bug" in body.splitlines()
    assert "7,用户反馈收集,done,medium," in body.splitlines()
