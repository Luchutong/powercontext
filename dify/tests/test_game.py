import itertools
import json

import pytest

from app import game
from scripts import build_dsl


def test_every_path_finishes_with_valid_bounded_save():
    endings = set()
    for choices in itertools.product("ABC", repeat=6):
        state = game.initial_state()
        for index, choice in enumerate(choices):
            state = game.advance(state, choice, f"event-{index}")
        assert state["turn"] == 6
        assert len(state["history"]) == 6
        assert not game.scene_view(state)["choices"]
        assert len(json.dumps(state, ensure_ascii=False).encode()) < 8192
        endings.add(state["ending"])
    assert endings == {"partners", "earth", "legend"}


def test_choice_changes_later_character_reaction_and_state():
    original = game.initial_state()
    shared = game.advance(original, "A", "a")
    sold = game.advance(original, "C", "b")
    assert original["turn"] == 0  # transitions cannot mutate the loaded head
    assert shared["stats"] == {"money": 65, "friendship": 3, "absurdity": 0}
    assert "让出了船票" in game.scene_view(shared)["situation"]
    assert "没有收到" in game.scene_view(sold)["situation"]


def test_illegal_and_post_ending_actions_do_not_advance():
    state = game.initial_state()
    with pytest.raises(game.InvalidAction):
        game.advance(state, "DELETE", "x")
    assert state["turn"] == 0
    for index in range(6):
        state = game.advance(state, "A", str(index))
    with pytest.raises(game.InvalidAction):
        game.advance(state, "A", "7")


def test_new_life_keeps_old_summary_without_unbounded_growth():
    state = game.initial_state()
    for life in range(7):
        for day in range(6):
            state = game.advance(state, "C", f"life-{life}-day-{day}")
        state = game.restart(state, f"restart-{life}")
    assert state["life"] == 8
    assert state["turn"] == 0
    assert len(state["past_lives"]) == 3
    assert len(json.dumps(state, ensure_ascii=False).encode()) < 8192


def run_code(source, **values):
    namespace = {}
    exec(source, namespace)
    return namespace["main"](**values)


def test_exact_dsl_input_code_handles_quotes_unicode_and_actions():
    result = run_code(build_dsl.NORMALIZE, passport="moon-demo-01", query='A 给小锈说"别怕"\n🌘', event_id="a-1")
    data = json.loads(result["body"])
    assert data["command"] == "A"
    assert data["user_note"] == '给小锈说"别怕"\n🌘'
    assert json.loads(run_code(build_dsl.NORMALIZE, passport="moon-demo-01", query="档案", event_id="a-2")["body"])["command"] == "ARCHIVE"


def test_dsl_failure_path_never_claims_saved_or_shows_uncommitted_prose():
    result = run_code(build_dsl.PARSE, body="Bad Gateway", status_code=502)
    assert result["needs_narration"] is False
    result = run_code(build_dsl.FINAL, body='{"detail":"conflict"}', status_code=409,
                      prose="这段剧情尚未保存", mode="AI")
    assert "这段剧情尚未保存" not in result["answer"]
    assert "未确认保存" in result["answer"]


def test_deepseek_reasoning_is_filtered_before_display():
    result = run_code(build_dsl.NARRATION, text="<think>internal scratchpad</think>小锈打开了锅盖。", fallback="备用剧情")
    assert result == {"prose": "小锈打开了锅盖。", "mode": "AI 叙事"}
    result = run_code(build_dsl.NARRATION, text="<think>unfinished", fallback="备用剧情")
    assert result["prose"] == "备用剧情"
    assert result["mode"] != "AI 叙事"
    result = run_code(build_dsl.NARRATION, text="<THINK>hidden</THINK>\n<!--dify-deepseek-reasoning-->明天照常营业。", fallback="备用剧情")
    assert result["prose"] == "明天照常营业。"
