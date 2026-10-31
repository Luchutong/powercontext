"""Target behaviour for the tag filter on GET /tasks (TAG-001..TAG-004)."""

import pytest

from taskboard.service import QueryError, search_tasks


def ids(result):
    return [item["id"] for item in result["items"]]


@pytest.mark.spec("TAG-001")
def test_search_tasks_returns_only_tasks_with_tag(tasks):
    result = search_tasks(tasks, {"tag": "backend"})
    assert ids(result) == [1, 4, 8, 6]
    assert result["total"] == 4
    assert all("backend" in item["tags"] for item in result["items"])


@pytest.mark.spec("TAG-001")
def test_unknown_tag_returns_empty_result(tasks):
    result = search_tasks(tasks, {"tag": "mobile"})
    assert result == {"items": [], "total": 0}


@pytest.mark.spec("TAG-002")
@pytest.mark.parametrize("raw", ["Backend", "  backend  ", "BACKEND"])
def test_tag_match_ignores_case_and_surrounding_whitespace(tasks, raw):
    result = search_tasks(tasks, {"tag": raw})
    assert ids(result) == [1, 4, 8, 6]


@pytest.mark.spec("TAG-003")
def test_tag_combines_with_status_and_keeps_order_limit_and_total(tasks):
    result = search_tasks(tasks, {"tag": "backend", "status": "open", "limit": "2"})
    assert ids(result) == [1, 8]
    assert result["total"] == 3


@pytest.mark.spec("TAG-001", "TAG-003")
def test_get_tasks_with_tag_over_http(client):
    status, body = client.get("/tasks", "tag=bug")
    assert status == 200
    assert ids(body) == [1, 5, 8]
    assert body["total"] == 3


@pytest.mark.spec("TAG-004")
@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_tag_is_rejected(tasks, raw):
    with pytest.raises(QueryError, match="tag"):
        search_tasks(tasks, {"tag": raw})


@pytest.mark.spec("TAG-004")
def test_blank_tag_returns_400_over_http(client):
    status, body = client.get("/tasks", "tag=")
    assert status == 400
    assert "tag" in body["error"]
