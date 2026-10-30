"""Original six-turn story. The model never decides balances or endings."""
from __future__ import annotations

import copy
import json
from pathlib import Path

STORY = json.loads((Path(__file__).parents[1] / "story/moon_stall.json").read_text(encoding="utf-8"))


class InvalidAction(ValueError):
    pass


def initial_state(life: int = 1, past_lives: list | None = None) -> dict:
    return {"schema": 1, "story_id": STORY["id"], "life": life, "turn": 0,
            "stats": {"money": 80, "friendship": 0, "absurdity": 0},
            "flags": [], "history": [], "ending": None, "last_event_id": "",
            "past_lives": (past_lives or [])[-3:]}


def advance(state: dict, choice: str, event_id: str) -> dict:
    if state["ending"] or state["turn"] >= len(STORY["scenes"]):
        raise InvalidAction("本段人生已经结束。输入「档案」回看，或「新人生」另开一段。")
    scene = STORY["scenes"][state["turn"]]
    if choice not in scene["choices"]:
        raise InvalidAction("请输入本场景的 A、B 或 C；也可以输入「继续」或「档案」。")
    option = scene["choices"][choice]
    result = copy.deepcopy(state)
    for name, delta in zip(("money", "friendship", "absurdity"), option["delta"], strict=True):
        result["stats"][name] += delta
    result["flags"].append(option["flag"])
    result["history"].append({"day": state["turn"] + 1, "scene": scene["title"],
                              "choice": choice, "label": option["label"], "result": option["result"]})
    result["turn"] += 1
    result["last_event_id"] = event_id
    if result["turn"] == len(STORY["scenes"]):
        result["ending"] = ("earth" if choice == "B" else
                            "partners" if choice == "A" and result["stats"]["friendship"] >= 6 else "legend")
    return result


def restart(state: dict, event_id: str) -> dict:
    previous = state["past_lives"] + [{"life": state["life"], "turns": state["turn"],
                                      "ending": state["ending"], "stats": state["stats"]}]
    result = initial_state(state["life"] + 1, previous)
    result["last_event_id"] = event_id
    return result


def scene_view(state: dict) -> dict:
    if state["ending"]:
        return {"title": STORY["endings"][state["ending"]]["title"],
                "situation": STORY["endings"][state["ending"]]["text"], "choices": []}
    scene = STORY["scenes"][state["turn"]]
    situation = scene["situation"]
    if state["turn"] == 1:
        situation += ("小锈还记得你昨天让出了船票，主动把天线拆下来帮你。" if "shared_ticket" in state["flags"]
                      else "小锈昨天没有收到你的船票，今天还在柜台前谨慎地询价。")
    return {"title": scene["title"], "situation": situation,
            "choices": [{"id": key, "label": value["label"], "delta": value["delta"]}
                        for key, value in scene["choices"].items()]}


def panel(state: dict, revision: int, saved: bool = True) -> str:
    stats = state["stats"]
    view = scene_view(state)
    lines = [f"### 🌘 {view['title']}",
             f"第 {state['life']} 段人生 · 已作出 {state['turn']}/6 次选择",
             f"月币 **{stats['money']}** · 人缘 **{stats['friendship']}** · 荒诞度 **{stats['absurdity']}**"]
    if state["history"]:
        lines.append("📌 记得你上次：" + state["history"][-1]["label"])
    lines.extend(f"**{option['id']}** · {option['label']}" for option in view["choices"])
    lines.append(f"🗃️ PowerContext 档案 v{revision} · " + ("已保存" if saved else "本次尚未保存"))
    lines.append("输入 A / B / C 做选择；「继续」续玩；「档案」回看；「新人生」保留旧版本重新开始。")
    return "\n\n".join(lines)


def archive(state: dict, revision: int) -> str:
    lines = ["## 🗃️ 人生不是白过的", f"第 {state['life']} 段人生 · PowerContext 档案 v{revision}"]
    for item in state["history"]:
        lines.append(f"**第 {item['day']} 天** · {item['label']}\n\n{item['result']}")
    if not state["history"]:
        lines.append("这是一份全新档案，你的第一个选择还没有发生。")
    if state["ending"]:
        lines.append("**结局：" + STORY["endings"][state["ending"]]["title"] + "**")
    for life in state["past_lives"]:
        title = STORY["endings"].get(life["ending"], {}).get("title", "未完待续")
        lines.append(f"旧人生 #{life['life']}：{title}，共 {life['turns']} 次选择。")
    lines.append("输入「继续」回到当前场景。")
    return "\n\n".join(lines)
