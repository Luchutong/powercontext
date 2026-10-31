"""Existing GET /tasks behaviour that every change must preserve (LIST-001..LIST-005)."""

import pytest

from taskboard.service import QueryError, search_tasks


def ids(result):
    return [item["id"] for item in result["items"]]


@pytest.mark.spec("LIST-001")
def test_default_listing_sorted_by_priority_then_id(tasks):
    result = search_tasks(tasks, {})
    assert ids(result) == [1, 4, 5, 2, 7, 8, 3, 6]
    assert result["total"] == 8


@pytest.mark.spec("LIST-002")
def test_status_filter_returns_matching_tasks(tasks):
    result = search_tasks(tasks, {"status": "open"})
    assert ids(result) == [1, 5, 2, 8, 6]
    assert {item["status"] for item in result["items"]} == {"open"}


@pytest.mark.spec("LIST-002")
def test_invalid_status_is_rejected_with_400(client):
    status, body = client.get("/tasks", "status=archived")
    assert status == 400
    assert "status" in body["error"]


@pytest.mark.spec("LIST-003", "LIST-004")
def test_limit_truncates_items_but_not_total(tasks):
    result = search_tasks(tasks, {"limit": "3"})
    assert ids(result) == [1, 4, 5]
    assert result["total"] == 8


@pytest.mark.spec("LIST-003")
@pytest.mark.parametrize("raw", ["0", "101", "ten"])
def test_invalid_limit_raises_query_error(tasks, raw):
    with pytest.raises(QueryError, match="limit"):
        search_tasks(tasks, {"limit": raw})


@pytest.mark.spec("LIST-004")
def test_response_shape_over_http(client):
    status, body = client.get("/tasks", "limit=1")
    assert status == 200
    assert set(body) == {"items", "total"}
    assert body["items"] == [
        {"id": 1, "title": "修复登录超时", "status": "open", "priority": "high", "tags": ["backend", "bug"]}
    ]


@pytest.mark.spec("LIST-005")
def test_unknown_query_parameters_are_ignored(client):
    status, body = client.get("/tasks", "status=done&sort=desc&page=2")
    assert status == 200
    assert ids(body) == [7, 3]
