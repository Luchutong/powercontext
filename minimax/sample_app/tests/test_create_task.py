"""POST /tasks behaviour (CRT-001..CRT-003)."""

import pytest

from taskboard.service import ValidationError, create_task


@pytest.mark.spec("CRT-001")
@pytest.mark.parametrize("title", ["", "   ", "x" * 81])
def test_title_is_required_and_bounded(tasks, title):
    with pytest.raises(ValidationError, match="title"):
        create_task(tasks, {"title": title})


@pytest.mark.spec("CRT-002")
def test_new_task_gets_next_id_and_open_status(client):
    status, body = client.post("/tasks", {"title": "整理发布清单", "priority": "low"})
    assert status == 201
    assert body["id"] == 9
    assert body["status"] == "open"
    assert body["priority"] == "low"


@pytest.mark.spec("CRT-003")
def test_tags_are_normalized_on_create(tasks):
    task = create_task(tasks, {"title": "补充监控", "tags": [" Ops ", "ops", "Backend", ""]})
    assert task.tags == ("ops", "backend")


@pytest.mark.spec("CRT-001")
def test_invalid_payload_returns_422_over_http(client):
    status, body = client.post("/tasks", {"title": "  "})
    assert status == 422
    assert "title" in body["error"]
