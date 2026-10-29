# 提测失败后的复查（2026-10-29）

本次修复了两个实际复现的故障，并补齐默认 Docker 启动验证。尚未取得比赛平台的失败日志，以下本地故障不能直接等同于平台给出的失败原因；本地通过也不代表已经获得平台分数。

## 已复现的问题与修复

1. **全新目录的模型下载超时。** 从首版 `11322f0c2a66c294b5cd8c266605958635188054` 建立隔离 worktree，执行原版 `bash scripts/start.sh --existing-db`。依赖安装成功，下载 `config.json` 时 `fetch failed / ETIMEDOUT` 并退出。原因是默认 Node fetch 不遵循当前 `HTTPS_PROXY`。现使用固定版本 undici 的 `EnvHttpProxyAgent`，支持代理与 NO_PROXY，加入超时、重试、固定文件大小/SHA-256 校验和原子替换；Node 22.12 与 24.16 都验证通过。默认启动仅获取 INT8/FP16 运行文件，FP32 对照文件按需补齐。
2. **数据库重启后持续 500。** 在保持浏览器和应用进程运行的情况下重启数据库，原实现继续使用 SDK 缓存的已关闭连接，返回 `Can't add new command when connection is in closed state`，数据库恢复健康后仍不能检索。原始错误见 [recheck-restart-before.json](recheck-restart-before.json)。现将连接检查置于完整数据库操作的串行队列内，失效时关闭旧连接、清理集合句柄并等待重新连接。不会自动重放可能已经提交的写入。
3. **修正启动配置。** seekdb 1.4 的 `memory_limit` 是已弃用的兼容参数，首版 Compose 的 `MEMORY_LIMIT=2G` 不能设置实际内存预算。现挂载明确配置 `memory_budget=2G` 的文件，并设置容器 3 GB 内存上限。健康检查等待 `.initialized` 和 SQL 就绪；应用与原生启动器统一读取 `.env`。这项配置修正不代表已经观察到平台因 OOM 失败。

## 验证范围

- **默认启动：** 隔离 worktree 应用修复差异，原模型目录为空、使用全新 Docker 数据卷；运行 `bash scripts/start.sh`，自动完成 npm ci、模型下载、新建数据库、健康检查、Vite 构建和应用启动。没有借用首版的模型目录或原生数据库。测试仅通过 `.env` 改用空闲端口 2885 / 4180 以隔离已有服务。
- **Docker：** `oceanbase/seekdb:1.4.0-100000172026082615`，拉取 digest 为 `sha256:4db4961869d554313520d6081fca29265ba6da6db7313311bd8acbed771a956b`。实际日志见 [recheck-docker.log](recheck-docker.log)；SQL 查询到的 CPU 2、memory_budget=2G、数据文件和日志参数见 [recheck-seekdb.json](recheck-seekdb.json)。首次启动和重启均通过。
- **Node 版本：** Node 22.12.0 与 24.16.0 的 8 项单元测试均通过；分别见 [Node 22 日志](recheck-unit-node22.txt)、[Node 24 日志](recheck-unit-node24.txt)。包含代理路由、临时服务故障/损坏下载重试、拒绝损坏权重覆盖、数据库延迟就绪和输入校验。Node 22 的真实代理下载返回 HTTP 200；生产构建日志见 [recheck-build-node22.txt](recheck-build-node22.txt)。
- **真实浏览器：** Docker 数据库上的 WASM、WebGPU 自动降级和软件 FP32 WebGPU 三条路径完整运行。文件导入、增量/重复写入、刷新后检索、集合切换、并发查询、页面恢复、移动布局和禁止非本机 HTTP 请求均检查。记录见 [recheck-docker-e2e.json](recheck-docker-e2e.json)；Node 22 服务上的独立浏览器操作另见 [recheck-node22-browser.json](recheck-node22-browser.json)。
- **原进程内恢复：** `SEEKDB_TEST_CONTAINER=stepfun_recheck-seekdb-1 APP_URL=http://127.0.0.1:4181 npm run test:restart`，浏览器与 Node 服务保持运行，只重启临时 Docker 数据库。确认原文及 ID 保留、旧连接恢复、重复导入幂等、新增资料可检索。详见 [recheck-restart.json](recheck-restart.json)。
- **性能与质量：** 对修复后的服务重新运行 3 种 WASM 配置、每配置 3 次、共 360 次向量/混合查询。使用相同 32 篇文档与 20 个标注问题，三种配置混合检索 Recall@5 均为 97.5%。最新计时见 [recheck-benchmark.json](recheck-benchmark.json)，表格见 [RESULTS.md](RESULTS.md)。此次 INT8 批 8/批 1 吞吐比为 0.99，未显示稳定的批处理收益；不沿用首版环境的 1.62 倍组合提升。首版五配置、600 次查询的历史记录仍保留为 [benchmark.json](benchmark.json)。

未获得硬件 WebGPU/FP16 环境，不能声称测量了硬件 FP16 的速度或精度。

## 平台提交条件仍需核对

官方题目要求从 `oceanbase/powercontext` 的 `2026-competition-ecosystem` 开发，产物放入 `stepfun/`；本仓库保留该基线历史。比赛平台为此题登记的仓库 URL 必须指向实际包含该分支和 commit 的仓库。

[官方 GitHub 说明](https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/qs1povt8zbza6hts)写明“一定要使用私有仓库进行测试”，并要求给判题端配置读取授权。复查时 `Luchutong/seekdb-TJU` 的可见性仍为 PUBLIC。没有擅自更改整个已有仓库的可见性，也不要求将任何 token 发到聊天中。

[官方 FAQ](https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/gp48z4pp0q3zg9b2)列出仓库不可访问、Git 历史无法检出、基线不符及分支/SHA 填反等常见失败。这些需结合平台实际登记信息与失败提示核查。GitHub commit status / workflow 查询没有返回本次比赛的判题日志。
