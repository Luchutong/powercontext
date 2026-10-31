# 规有所依——面向软件持续演进的 SDD 上下文管理系统（MiniMax × OceanBase）

为 AI 开发助手实现的工程上下文模块：把规格、代码、测试导入 **seekdb**，按开发任务查询出适用的规格条目、
需要修改的代码与验证测试；把上下文交给 **MiniMax** 完成一次真实的代码修改；运行功能与回归测试；用
**PowerContext** 保存任务进展与验证结果，重启后读回并用于后续上下文与任务处理。

## 快速开始

```bash
cd minimax
bash scripts/install.sh        # 创建 .venv，安装锁定版本的依赖（推荐先安装 uv）
bash scripts/demo_accept.sh    # 从空状态跑完全流程并自动核对六项验收，约 70 秒
```

不需要 Docker、数据库服务或 API Key：seekdb 以嵌入式方式运行，PowerContext Server 在本机启动，MiniMax 步骤默认回放
录制的真实会话。设置 `MINIMAX_API_KEY` 后可用 `SDD_EDIT_MODE=live` 实时调用 MiniMax 官方 API。

## 文档

| 文档 | 内容 |
| --- | --- |
| [运行说明.md](运行说明.md) | 依赖版本、安装、配置、数据初始化、调用入口（CLI 输入/输出）、测试命令、重置、已知限制 |
| [验证样例.md](验证样例.md) | 任务描述（要改什么 / 必须保持什么）、有效规格、初始数据、上下文必须包含的来源、操作步骤与预期结果、重置方法 |
| [技术使用记录.md](技术使用记录.md) | MiniMax 开发记录、seekdb 查询记录、PowerContext 状态写入与复用记录 |
| [一页技术说明.md](一页技术说明.md) | 模块结构、上下文选择方式、各项技术分工 |
| [evidence/验收结果.md](evidence/验收结果.md) | 本仓库提交的一次完整运行的六项验收核对明细 |

## 六项必做项与证据

| 验收项 | 如何满足 | 核对位置 |
| --- | --- | --- |
| 能够运行 | `install.sh` + `demo_accept.sh` 从空状态完成初始化、导入、上下文、测试、修改、状态保存与重启、规格更新，不依赖未交付的文件或状态 | `evidence/demo_steps.tsv`（24 步命令与退出码）、`evidence/demo_accept.log` |
| MiniMax 实际参与 | 录制的真实 MiniMax 会话包含任务、提供的完整上下文、回复、写入的文件、diff 与每轮测试结果；回放时提示词逐字节一致、diff 与 `evidence/code.diff` 和前后快照一致 | `evidence/minimax/开发记录.md`、`session_recorded.json`、`replay_check.json`、`evidence/code.diff`、`snapshots/` |
| 上下文正确 | 输出 TAG-001～004（变更）、LIST-001～005（保持）、`taskboard/service.py::search_tasks`、7 个目标测试与 7 个回归测试、done/todo/验证结果，来源带行号与 sha256；规格更新为 v2 并重新导入后输出 TAG-001 rev 2 新正文与新增 TAG-005 | `evidence/context_v1.md`、`context_v2.md`、`sample_task/required_sources.json` |
| 数据库实际参与 | 资料只存于嵌入式 seekdb；上下文每一项都由 SQL（含 ik 全文检索）查询得到并标注查询编号，SQL 与返回行全部记录 | `evidence/seekdb_queries.log`、上下文 JSON 的 `via` 与 `retrieval` |
| 状态能够复用 | 从空的 PowerContext 开始写入 Memory、验证记录、Handoff 等；停止并重启 Server 后，新进程读回 `handoff@2` 与验证记录，用于上下文的 done/todo、跳过重复修改、规格变更后的失效判断 | `evidence/state/*.json`、`evidence/context_resume.md` |
| 代码修改有效 | 同一组 10 个目标用例修改前全部因断言失败（无环境错误），修改后全部通过；回归 9/9、全量 27/27 通过；测试文件摘要前后一致 | `evidence/tests/before-*.log`、`after-*.log` |

## 目录

```text
minimax/
├── sdd, sdd_context/           上下文模块（CLI 入口与实现）
├── sample_app/                 业务工程（修改前版本）：specs/、taskboard/、tests/、data/
├── sample_task/                任务定义、v2 规格更新、评审核对清单
├── scripts/                    install.sh、demo_accept.sh、reset.sh
├── evidence/                   一次完整运行的产物（上下文、查询日志、测试日志、状态记录、MiniMax 会话、验收结果）
├── snapshots/before|after/     修改前后代码快照
├── module_tests/               上下文模块自身的测试
├── requirements*.txt, .env.example
└── 运行说明.md、验证样例.md、技术使用记录.md、一页技术说明.md
```

复用说明：PowerContext、seekdb（pylibseekdb）、MiniMax 均通过其公开发布的包或接口使用，未修改其源码；样例工程、规格与测试为本作品原创。
