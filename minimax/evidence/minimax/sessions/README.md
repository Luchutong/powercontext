# MiniMax 会话原始记录

两次都是对 MiniMax 官方 Hugging Face Space `MiniMaxAI/MiniMax-Text-01` 的真实调用，首轮提示词完全相同
（`prompt_sha256 = de92e0542ccf…`，由 `sdd_context` 根据 seekdb 查询结果和 PowerContext 状态生成）。

| 会话 | 用途 | 结果 |
| --- | --- | --- |
| `mmx-20261028T041139Z-e4c76c.json` | **正式记录**，与 `../session_recorded.json` 内容相同，`demo_accept.sh` 回放的就是它 | 第 1 轮即让上下文中的目标 + 回归测试 19/19 通过；diff 即 `evidence/code.diff` |
| `mmx-20261028T040706Z-7cb5e1.json` | 开发 `edit` 命令时的第一次单轮调用（当时还没有“测试反馈→再修改”的回路），未被采用 | 补丁把“未提供 tag”也当成空标签抛出 `QueryError`，回归测试 8 个用例失败（见 `mmx-20261028T040706Z-7cb5e1.check.log`）。这次失败促成了 `edit` 的反馈回路：每轮修改后运行目标 + 回归测试，失败信息作为下一轮输入发回 MiniMax（最多 3 轮） |

早期记录的格式（单轮、无 `attempts` 字段）比正式记录旧，只作为开发过程留档。
