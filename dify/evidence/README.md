# 运行证据与证明范围

证据按实际运行类型区分：

| 记录 | 能证明什么 | 不能替代什么 |
| --- | --- | --- |
| 游戏规则 pytest | 729 条决策路径均能结束、三种结局、状态大小与非法行动处理 | Dify 导入、HTTP 网络和真实模型 |
| PowerContext 集成 pytest | 真正的 Scope、Atomic Memory 读写、重复提交、冲突、分区、完整结局 | 真实 Dify 图执行 |
| `memory-verification.json` | 实际 HTTP 写入后，用另一个 OS 进程读回；历史进入下一轮提示词 | Dify 执行和真实模型叙事 |
| Dify 节点契约检查 | 官方 Dify 1.17.1 / graphon 0.7.0 能解析全部节点数据 | 应用已导入、发布并跑通 |
| `dify-live/runs.json` | Dify 1.17.1 实际导入、节点执行、两个会话、完整六天结局、重新开始、发布 | 此记录为独立剧本验证版，未调用模型 |
| `cloud-live.json` | 用户 Dify Cloud 发布的正式 AI 主应用，通过其应用 API 真实调用、保存并跨会话恢复 | 对未来每次开放式文本输出的保证 |
| `restart-persistence.json` | 配套服务容器进程真正重启后，Artifact ID、版本和状态完全一致 | 高可用、多副本或灾备保证 |
| `tunnel-preflight.json` | 演示期 HTTPS 地址可达，带凭证 200、无凭证 401 | 临时地址长期可用 |

当前已执行的检查：

- Windows Python 3.13.5、PowerContext 1.4.1：`TEST_PC_URL=http://127.0.0.1:18380 python -m pytest tests -q`，**10 passed in 59.32s**。
- 加入 DeepSeek 思考标签回归与状态版本读回修正后，最终结果为 **11 passed in 69.33s**；见 `pytest.log`。
- Dify 官方 `langgenius/dify-api:1.17.1` 中，`validate_dsl.py` 已解析 15 个节点契约。
- 本地 Dify 干净重导入独立基础版：`import.status=completed`，无警告；11 轮实际执行，含两个会话、6 次选择、v7 结局、v8 新人生；发布成功。
- Dify Cloud 正式 AI 主版：开始、A、另一个会话继续、档案四轮全部通过；前三轮返回「AI 叙事」，真实模型用量与脱敏执行事件记录在 `cloud-live.json`。
- 最终云端回归四轮 `raw_reasoning_detected` 均为 false，选择 A 后为 v2，新会话恢复同一进度。已从实际导出包核对 DeepSeek 0.0.26 / deepseek-flash；交付版关闭思考模式，并分离、过滤思考输出。
- `prove_restart.py`：容器 StartedAt 和进程 PID 均改变；存档身份、v2 与完整状态保持一致。

回放、原创剧本与真实模型分别标记。证据不包含模型密钥、应用凭据、签名票据或管理员会话。演示口令与游戏内容均为本次验收生成的合成数据。
