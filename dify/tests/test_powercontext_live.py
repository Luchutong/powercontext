"""Opt-in integration checks against real PowerContext, never a mocked memory DB."""
import os
import secrets

import pytest
from fastapi.testclient import TestClient

from app.server import PowerContext, create_app

pytestmark = pytest.mark.skipif(not os.getenv("TEST_PC_URL"), reason="set TEST_PC_URL to a fresh real PowerContext Server")
TOKEN = "test-token-" + "x" * 32
SIGNING = "test-signing-" + "z" * 48


@pytest.fixture
def client():
    return TestClient(create_app(PowerContext(os.environ["TEST_PC_URL"]), TOKEN, SIGNING))


def prepare(client, passport, command, event):
    result = client.post("/game/prepare", headers={"Authorization": "Bearer " + TOKEN},
                         json={"passport": passport, "command": command, "event_id": event})
    assert result.status_code == 200, result.text
    return result.json()


def commit(client, ticket):
    return client.post("/game/commit", headers={"Authorization": "Bearer " + TOKEN}, json={"ticket": ticket})


def test_real_write_new_process_style_read_and_user_partition(client):
    passport = "live-" + secrets.token_hex(10)
    turn = prepare(client, passport, "A", "choice-1")
    saved = commit(client, turn["ticket"])
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == 2
    # A fresh application has no in-memory player state or registry.
    fresh = TestClient(create_app(PowerContext(os.environ["TEST_PC_URL"]), TOKEN, SIGNING))
    restored = prepare(fresh, passport, "ARCHIVE", "other-conversation")
    assert restored["state"]["history"][0]["choice"] == "A"
    assert restored["artifact_id"] == saved.json()["artifact_id"]
    other = prepare(fresh, "other-" + secrets.token_hex(10), "ARCHIVE", "other-player")
    assert other["state"]["turn"] == 0
    assert other["artifact_id"] != restored["artifact_id"]


def test_repeated_commit_is_idempotent_and_stale_turn_cannot_overwrite(client):
    passport = "live-" + secrets.token_hex(10)
    a = prepare(client, passport, "A", "concurrent-a")
    b = prepare(client, passport, "C", "concurrent-b")
    first = commit(client, a["ticket"])
    second = commit(client, a["ticket"])
    stale = commit(client, b["ticket"])
    assert first.status_code == second.status_code == 200
    assert second.json()["deduplicated"] is True
    assert second.json()["revision"] == first.json()["revision"]
    assert stale.status_code == 409
    state = prepare(client, passport, "ARCHIVE", "after-conflict")["state"]
    assert state["turn"] == 1 and state["stats"]["money"] == 65


def test_tampered_ticket_and_unauthorized_access_rejected(client):
    assert client.post("/game/prepare", json={"passport": "abcdefgh", "command": "A", "event_id": "x"}).status_code == 401
    turn = prepare(client, "live-" + secrets.token_hex(10), "A", "tamper")
    assert commit(client, turn["ticket"] + "bad").status_code == 400


def test_full_game_archives_ending_and_preserves_previous_life(client):
    passport = "live-" + secrets.token_hex(10)
    for day in range(6):
        turn = prepare(client, passport, "A", f"day-{day}")
        response = commit(client, turn["ticket"])
        assert response.status_code == 200, response.text
    assert response.json()["state"]["ending"] == "partners"
    snapshot = prepare(client, passport, "ARCHIVE", "archive")
    assert "银河合伙人" in snapshot["reply"]
    turn = prepare(client, passport, "NEW", "new-life")
    response = commit(client, turn["ticket"])
    assert response.status_code == 200
    assert response.json()["state"]["life"] == 2
    assert response.json()["state"]["past_lives"][0]["ending"] == "partners"
