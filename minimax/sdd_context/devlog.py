"""Render a recorded MiniMax session as the human-readable development record (开发记录.md)."""

from __future__ import annotations

import re
from typing import Any

CODE_BLOCK = re.compile(r"```[A-Za-z0-9_+-]*\n.*?\n```", re.S)


def _explanation(text: str) -> str:
    without_code = CODE_BLOCK.sub("", text)
    lines = [line for line in without_code.splitlines() if line.strip() and not line.startswith("FILE:")]
    return "\n".join(lines).strip() or "（回复中没有额外说明）"


def render_devlog(session: dict[str, Any]) -> str:
    task = session["task"]
    ctx = session["context"]
    result = session["result"]
    attempts = session["attempts"]
    progress = " → ".join(f"第 {a['attempt']} 轮 {a['check']['passed']}/{a['check']['total']} 通过" for a in attempts)
    lines = [
        f"# MiniMax 开发记录：{task['id']} {task['title']}",
        "",
        "本文件由 `./sdd edit --mode hf-space|live --record` 根据会话 JSON 自动生成；"
        "逐字内容（提示词、各轮回复、反馈、diff、测试结果）以 "
        "[`session_recorded.json`](session_recorded.json) 为准。模型私有思维链不在记录范围内。",
        "",
        "| 项目 | 内容 |",
        "| --- | --- |",
        f"| 会话 ID | `{session['session_id']}` |",
        f"| 时间（UTC） | {session['created_at']} |",
        f"| 调用通道 | `{session['transport']}`：{session['endpoint']} |",
        f"| 模型 | {session['model']} |",
        f"| 请求参数 | `{session.get('request_params')}` |",
        f"| 修改轮次 | {len(attempts)}（{progress}） |",
        f"| 修改文件 | {', '.join('`' + f['path'] + '`' for f in result['files'])} |",
        f"| 最终 diff | [`evidence/code.diff`](../code.diff)，sha256 `{result['diff_sha256']}` |",
        f"| 快照 | `snapshots/before/`、`snapshots/after/` |",
        "",
        "## 1. 本次任务",
        "",
        f"`{task['id']}` {task['title']}",
        "",
        task["description"],
        "",
        "## 2. 提供给 MiniMax 的上下文",
        "",
        "上下文由 `sdd_context` 从 seekdb 查询得到（查询编号 " + ", ".join(ctx["seekdb_queries"]) + "），任务状态来自 PowerContext：",
        "",
        f"- 需要实现的规格（变更）：{', '.join(ctx['change_specs'])}",
        f"- 必须保持的规格：{', '.join(ctx['preserve_specs'])}",
        "- 代码目标：" + "；".join(f"{t['symbol']}（{'修改' if t['role'] == 'modify' else '相关'}）" for t in ctx["code_targets"]),
        f"- 目标测试：{len(ctx['target_tests'])} 个；回归测试：{len(ctx['regression_tests'])} 个",
        f"- PowerContext 状态：{ctx['state_status']}（已完成 ingest、context、baseline；待办 edit、verify）",
        f"- seekdb 导入批次：#{ctx['import']['import_id']}，spec_digest `{ctx['import']['spec_digest']}`",
        f"- 首轮提示词 sha256：`{session['request']['prompt_sha256']}`（全文见附录 A）",
        "",
        "## 3. 修改过程",
        "",
    ]
    for attempt in attempts:
        check = attempt["check"]
        lines += [f"### 第 {attempt['attempt']} 轮", ""]
        if attempt["feedback"]:
            lines += ["发送给 MiniMax 的测试反馈（原文）：", "", "```text", attempt["feedback"], "```", ""]
        lines += [
            f"- 请求 sha256：`{attempt['request_sha256']}`；耗时 {attempt['latency_s']} s",
            f"- MiniMax 的说明：",
            "",
            "> " + _explanation(attempt["response"]["text"]).replace("\n", "\n> "),
            "",
            f"- 应用后的代码差异（相对修改前文件，sha256 `{attempt['diff_sha256'][:16]}`）：",
            "",
            "```diff",
            attempt["diff"].rstrip(),
            "```",
            "",
            f"- 回复无法应用：{check['apply_error']}" if check.get("apply_error") else
            f"- 运行上下文中的目标 + 回归测试：`{check['command']}` → exit {check['exit_code']}，{check['passed']}/{check['total']} 通过",
        ]
        if check["failures"]:
            lines.append("- 失败用例：")
            lines += [f"  - `{f['nodeid']}`：{f['message']}" for f in check["failures"]]
        lines.append("")
    lines += [
        "## 4. 与提交的代码差异的对应关系",
        "",
        "| 核对项 | 值 |",
        "| --- | --- |",
        f"| 会话最终 diff sha256 | `{result['diff_sha256']}` |",
        "| `evidence/code.diff` | 由最终轮次写出，sha256 与上一行相同（`./sdd accept` 自动核对） |",
    ]
    for entry in result["files"]:
        lines.append(f"| `snapshots/before/{entry['path']}` sha256 | `{entry['before_sha256']}` |")
        lines.append(f"| `snapshots/after/{entry['path']}` sha256 | `{entry['after_sha256']}` |")
    lines += [
        "",
        "回放（`./sdd edit --mode replay`，`demo_accept.sh` 默认方式）会重新生成首轮提示词并与本记录逐字节比较，"
        "再依次应用每一轮回复、重跑测试，核对每轮 diff 与测试结果，结果写入 `evidence/minimax/replay_check.json`。",
        "",
        "## 附录 A：首轮完整提示词",
        "",
    ]
    for message in session["request"]["messages"]:
        lines += [f"### role: {message['role']}", "", "````markdown", message["content"], "````", ""]
    lines += ["## 附录 B：各轮完整回复", ""]
    for attempt in attempts:
        lines += [f"### 第 {attempt['attempt']} 轮回复", "", "````markdown", attempt["response"]["text"].rstrip(), "````", ""]
    return "\n".join(lines)
