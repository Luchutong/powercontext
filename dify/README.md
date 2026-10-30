# 平行人生档案馆 · 月球最后一家煎饼摊

**六天、三种结局。一口锅、一位机器人，以及会留下来的选择。**

2046 年，你在月球失业了。你只剩 80 月币、一口锅，以及没有味觉却自认美食家的机器人小锈。撤离船六天后离港：你要赚钱、交朋友，还是成为银河最离谱的传说？

这是 Dify × OceanBase《Just for Fun》作品。选择 **Dify Chatflow + PowerContext**；PowerContext 使用 SQLite 持久化。本作品没有使用 seekdb，不需要 Embedding 或 Rerank。

## 玩法

1. 在 Dify 应用中填写一个 8～64 位的「人生口令」，例如 `moon-demo-2026`。口令只使用英文字母、数字、下划线或短横线。同一口令关联同一档案。
2. 发送「开始」，随后回复 A / B / C 做选择；可以补充一句台词，如 `A 告诉小锈：别怕，锅还在`。
3. 查看剧情、月币、人缘、荒诞度和真实存档版本。
4. 新开 Dify 会话，填写**相同口令**，发送「继续」，从上次的场景续玩。
5. 输入「档案」回看选择；输入「新人生」重新开始，旧人生摘要和 PowerContext 历史版本仍保留。

角色会记得你是否让出了船票。第六天根据最后选择和人缘进入「银河合伙人」「地球归来者」「月球传说」之一。这里没有唯一正确的人生。

## 技术分工

| 组件 | 实际工作 |
| --- | --- |
| Dify Chatflow | 输入整理、HTTP 工具调用、条件分支、DeepSeek 模型叙事、失败兜底、提交存档及最终输出 |
| PowerContext 1.4.1 | 每个口令对应独立 Scope；Atomic Memory 保存游戏状态；后续互动读回并使用选择；正文修订带 ETag，并保留历史版本 |
| 配套 Python 服务 | 校验行动、计算数值与结局、签发回合票据、通过 HTTP 调用 PowerContext；不调用模型，不在本地字典或另一数据库保存权威进度 |

只有真实存档成功并读回后才显示「已保存」。重复提交不会重复扣款；过期回合不能覆盖新进度。Dify 的 `conversation_id` 不作为存档身份。

## 运行版本与依赖

- 本地验收目标：Dify **1.17.1**，官方 commit `8387590ace4a094de812b7847fc6a4c3a27cd52b`，DSL **0.7.0**。
- 正式 AI 体验另在 Dify Cloud（2026-10-30）实测，导出 DSL 为 0.7.0；云端主机版本由平台托管，模型、插件版本与实际执行证据单独记录。
- PowerContext：`powercontext[cli,server]==1.4.1`，使用该安装版本实际公开的 Atomic Memory HTTP API。
- Python：Docker 使用 3.12；Windows 原生开发环境使用 3.13.5。
- Dify 模型供应商：`langgenius/deepseek/deepseek`，DSL 预选 `deepseek-flash`；导入后可重新选择账号中已配置的兼容聊天模型。模型密钥只配置到 Dify 的模型供应商中。
- 云端实测插件版本：DeepSeek **0.0.26**，安装来源为 Dify Marketplace。主 DSL 固定其完整插件标识，来源是实测后从 Dify 导出的应用包，详见 `evidence/model-binding.json`。
- `requirements.txt` 是服务依赖，`requirements-dev.txt` 追加 pytest。Dify 的 Python Code 节点只使用标准库。
- `requirements-linux-py312.lock` 固定已验收 Linux Python 3.12 容器中的 120 个依赖版本，Docker 构建使用此锁定文件；原生 Windows 开发使用前述跨平台依赖文件。
- 无知识库、外部故事数据、自定义 Dify 插件或图片/音频依赖。原创故事数据完整放在 `story/moon_stall.json`。
- 自托管 Dify 需要 Docker/Compose 和网络下载能力；建议至少 2 核、4 GiB 可用内存，并为镜像预留磁盘。部署脚本保留 SSRF 防护，只允许它访问本作品的 `life_tools` 私网服务名。

### 两种叙事模式

- `narration_mode=ai`：由 Dify 的 DeepSeek 节点生成叙事。需要配置并选择可用模型。
- `narration_mode=script`：使用随作品交付的原创剧本文本，完整游戏、记忆和存档流程照常运行；明确显示「未调用模型」。适合先检查配置与接线。

主 DSL 默认 `ai`。Dify 会在执行前检查图中的模型，即使该轮不走 AI 分支，也可能要求供应商存在并完成配置。没有模型环境时，请导入独立的 `parallel-life.script-check.yml` 基础验证版，它完全不包含模型节点。AI 调用失败会明确标记兜底方式。实际证据必须区分模型叙事与原创剧本，不把剧本运行当成真实模型运行。

DeepSeek 输出中的 `<think>...</think>` 不进入用户剧情；若思考标签未闭合、没有可用剧情，则使用明确标注的原创剧本兜底。数值、选项与保存状态仍来自确定的规则和实际存档回执。

正式模型参数固定 `thinking=false`，LLM 节点使用 `reasoning_format=separated`。DeepSeek 插件默认开启思考模式，故显式关闭，避免平台提前把思考流附加到游戏回复；应用只需要短篇创作，不需要推理过程展示。

## 方式一：已有 Dify / Dify Cloud

先启动下述配套服务，然后选择要导入的 DSL。两份是独立应用，无先后依赖：

| DSL | 用途 |
| --- | --- |
| `dsl/parallel-life.chatflow.yml` | **正式 AI 主应用**，15 节点；绑定 DeepSeek 后体验智能叙事 |
| `dsl/parallel-life.script-check.yml` | 可选的基础验证应用，12 节点；不调用模型，验证原始剧本、游戏和记忆链路，不能替代 AI 效果验收 |

支持应用包的 Dify 版本也可导入 `releases/parallel-life.ifpkg`；它与正式主 DSL 等价，无须两者同时导入。包按 Dify 导出格式包含 `app.yaml`、大小与 SHA-256 清单，不包含 Secret 值。

在本目录执行：

```powershell
uv venv .runtime/venv
uv pip install --python .runtime/venv/Scripts/python.exe -r requirements.txt
$env:PYTHONUTF8='1'
.runtime/venv/Scripts/python.exe scripts/configure.py
```

两个终端分别运行：

```powershell
.runtime/venv/Scripts/python.exe scripts/run_powercontext.py
```

```powershell
.runtime/venv/Scripts/python.exe scripts/run_gateway.py
```

PowerContext 数据位于 `.runtime/powercontext/`，配套服务凭据位于 `.runtime/app.env`，都被 Git 忽略。PowerContext 仅监听本机 `127.0.0.1:18380`；配套服务默认为 `127.0.0.1:18381`。

Linux/macOS 将 `.runtime/venv/Scripts/python.exe` 替换为 `.runtime/venv/bin/python`，并用 `export PYTHONUTF8=1`。生产部署可以采用下述容器服务，或在受控服务器上配置相同环境；本机回环监听不能直接被另一台机器访问。

在 Dify Studio 导入 DSL 后，配置应用环境变量：

| 变量 | 如何填写 |
| --- | --- |
| `life_service_url` | 从 Dify API/HTTP 节点能访问的配套服务地址，不是 PowerContext 的 18380 端口 |
| `life_service_token` | `.runtime/app.env` 中的 `APP_TOKEN`；保持 Secret 类型 |
| `narration_mode` | 主应用默认 `ai`；绑定模型后也可切换 `script` 比较原始剧本。基础验证版无此变量 |

打开「月球小说家」节点，选择已安装的 DeepSeek 供应商和可用聊天模型。默认预选模型不存在时，重新选择即可，无需改游戏规则。

**Dify Cloud 无法访问开发机的 `localhost`、`127.0.0.1` 或 Docker 服务名。** 必须给它配置可访问的 HTTPS 服务地址；可以在自己的服务器部署，或在演示期间通过受保护的反向代理/临时隧道访问本机配套服务。仅代理 18381 的游戏服务，保留 Bearer 认证，不公开 PowerContext 管理端口。临时地址失效后需要重新配置，不能将作者电脑长期在线作为评审复现前提。

本作品的 Docker 部署把游戏端口映射到本机 `127.0.0.1:18401`。Linux/WSL 演示时，可自行选择用 Cloudflare 临时中转该端口；它会经过 Cloudflare 传输游戏输入、状态与专用接口凭证：

```bash
docker run --rm --network host cloudflare/cloudflared:2026.10.6 tunnel --no-autoupdate --url http://127.0.0.1:18401 --protocol http2
```

使用终端返回的 HTTPS 地址生成只保存在忽略目录中的专用导入包：

```bash
python scripts/export_cloud_import.py --service-url https://your-game-service.example
```

导入 `.runtime/parallel-life.cloud-update.ifpkg`，在「月球小说家」节点选择模型，然后完成「发布 → 发布更新」。该私有包带有游戏服务专用凭证，不能提交到仓库。正式交付的 `releases/parallel-life.ifpkg` 不包含它。

如需自动核验 Dify Cloud，将**当前已发布应用**的 API Key 填入 `.runtime/cloud.env`，API Base URL 以该应用「访问 API」页面为准，执行 `python scripts/verify_cloud.py`。它会调用三次模型并进行一次只读档案查询；验证开始、选择、另一会话恢复和历史查看，且拒绝把思考标签或剧本兜底当成真实 AI 验收。应用 API Key 与 DeepSeek 供应商 Key 是不同的凭据。

若使用自托管 Dify Docker 连接宿主机服务，应配置正确的宿主机地址、监听与精确 SSRF 允许项。浏览器能访问某地址，不代表 Dify API 容器能访问它。

## 方式二：隔离的本地 Docker 验收环境

在本目录使用 Python 环境执行：

```bash
python scripts/configure.py
python scripts/prepare_dify.py
docker compose -f .runtime/dify-host/compose.yaml --project-name parallel-life up -d --build
```

准备脚本会下载固定 Dify 官方源码并校验 commit，生成 `.runtime/dify-host/` 下的独立部署；也可用 `--source /path/to/dify-1.17.1` 使用已有的精确版本源码。它不会修改源码，也不会操作其他 Docker 项目。

该部署针对经典 Chatflow，只启动所需 API、worker、web、数据库、sandbox、plugin daemon、SSRF proxy、Nginx，以及本作品服务；不启动未使用的新 Agent/向量数据库。端口只映射本机 `http://localhost:18400`，数据使用 `parallel-life` 项目命名的数据卷。无 seekdb 使用声明。

1. 打开 `http://localhost:18400/install`，完成本地管理员初始化。
2. 有模型时导入主应用 `dsl/parallel-life.chatflow.yml`；没有模型时导入独立基础验证版 `dsl/parallel-life.script-check.yml`。
3. `life_service_url` 使用默认 `http://life_tools:18381`；Secret 填写配置中生成的 APP_TOKEN。
4. 按验证样例体验。正式 AI 主应用需安装并配置 DeepSeek 供应商、选择模型；基础验证版可直接运行，不会尝试调用模型。

停止本项目但保留数据：

```bash
docker compose -f .runtime/dify-host/compose.yaml --project-name parallel-life stop
```

重新 `up -d` 可继续。不要用删除卷的命令作为普通停止方式。

## 验证、检查与证据

完整体验样例、初始状态、预期表现和重置方法见 [samples/verification.md](samples/verification.md)。实际证据的类型和局限见 [evidence/README.md](evidence/README.md)。

游戏规则与集成测试：

```powershell
uv pip install --python .runtime/venv/Scripts/python.exe -r requirements-dev.txt
$env:TEST_PC_URL='http://127.0.0.1:18380'
.runtime/venv/Scripts/python.exe -m pytest tests -q
.runtime/venv/Scripts/python.exe scripts/verify_memory.py
```

Linux/macOS：`TEST_PC_URL=http://127.0.0.1:18380 python -m pytest tests -q`。未设置 TEST_PC_URL 时，实际服务集成用例会明确跳过；纯规则测试不能替代完整验收。

`scripts/inspect_save.py --passport <演示口令>` 可通过真实 PowerContext HTTP API 定位或初始化 Scope 并读回档案。只能在信任的本机环境使用，不输出凭据。`verify_memory.py` 会生成独立进程读回与历史用于后续提示词的证据，**不声称执行了 Dify 或模型**。

`scripts/build_dsl.py` 用于重建两份 DSL；`scripts/validate_dsl.py` 需在固定 Dify 官方镜像中运行，验证真实节点类型契约。`scripts/verify_dify.py` 只在独立本地 Dify 上初始化测试管理员，导入并执行基础验证版，不操作 Dify Cloud。凭据在忽略目录；静态校验不能代替实际导入和运行。

## 目录与来源

- `dsl/`：正式 AI Chatflow 与独立基础验证版；`app/`：规则引擎与 HTTP 配套源码；`story/`：完整原创数据。
- `scripts/`：配置、部署、生成、检查与验证；`tests/`：规则与真实服务集成测试。
- `samples/`：评审可执行样例；`evidence/`：脱敏实际记录；`.runtime/`：本机依赖、数据、凭据，禁止提交。

故事、规则、提示词与应用编排为本作品编写。DSL 格式和部署生成依据 Dify 1.17.1 官方源码（Dify Open Source License，基于 Apache-2.0 并有附加条款，见其 LICENSE）；PowerContext 使用官方 1.4.1 HTTP API（Apache-2.0）。没有复用其他参赛作品或公开娱乐工作流。开发使用 Codex 辅助；运行证据以实际记录为准。

## 已知限制

- 一台配套服务、一个 worker；首次建档的进程锁不支持多副本并发初始化。后续状态写入仍受 PowerContext ETag 保护。
- 人生口令是匿名演示的档案访问口令，不是用户账户体系；请使用随机口令，不输入个人隐私。正式多用户部署需增加身份认证和独立授权。
- 剧情是六个固定决策阶段，AI 扩写叙事；不会生成无限世界或让模型修改游戏数值。
- 每条状态 Memory 不超过 8192 UTF-8 字节；只保留最近三段旧人生摘要，完整历史仍可通过 PowerContext 的不可变正文版本读取。
- 断网、冲突或超时不显示虚假保存成功。遇到结果未知，先「继续」读回，再决定下一步。
- 公网 Dify、模型供应商和镜像/依赖下载需要相应网络和账号；真实模型调用可能消耗用户已配置服务的额度。

## 比赛提交

本分支从个人参赛仓库的 `2026-competition-ecosystem` 建立，保留官方生态基线 `d647275c308e09b7b623f322a26f5c4e85734fb0`。全部项目资产位于 `dify/`，其他赛道目录保持原样。在测评平台填写本作品所在分支或准确 commit hash；不要误填不含本作品的旧版本。
