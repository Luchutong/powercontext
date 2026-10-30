# 📚 知库 ZhiKu

**把组会纪要、论文笔记、周报和规章制度收进本地资料库：一句话找到原文，并持续跟踪纪要中的待办。**

| 提交材料 | 位置 / 当前状态 |
| --- | --- |
| 项目网站（Qoder Sites） | <https://zhiku-seekdb-db1ghqb6plk.qoder.zone>；已发布并设为公开，无痕 Chrome 验证通过 |
| 代码仓库 | <https://github.com/Luchutong/powercontext/tree/2026-competition-ecosystem/qoder> |
| 运行说明 | 本文 [运行说明](#运行说明) |
| 验证样例 | 本文 [验证样例](#验证样例)，实际结果见 [docs/verify-log.md](docs/verify-log.md) |
| Qoder 开发记录 | [docs/qoder-records/](docs/qoder-records/)；账号 UID 尚未填写 |
| 本轮开发起点 | `0f239d35430ded99f35705f9df226b5ae25917d1` |
| 提测 Commit | 以推送后的 `2026-competition-ecosystem` 分支 HEAD 为准，通过 `git rev-parse HEAD` 获取 |
| 使用效果 | 尚未收集真实使用数据，不填写估算结果 |

## 解决什么问题

科研和工作中，真正耗时的往往不是做事，而是“找到之前定下来的那件事”：

- 资料分散在不同文件夹、不同格式里，找一句话要挨个打开；
- 记得意思却记不清原话，Ctrl+F 搜“发票什么时候交”搜不到“当月 25 日前提交给财务”；
- 任务负责人和截止日期写在纪要里，完成进度又只存在网页或口头沟通中，过几天还要重新翻。

知库用 seekdb 的「全文 + 语义」混合检索返回相关段落和出处，并把 Markdown 任务列表同步成可筛选、可持久化的待办。

## 核心功能

| 功能 | 说明 |
| --- | --- |
| 一键导入 | 读取文件夹里的 Markdown / TXT / PDF，按段落切成约 400 字的片段；网页里也可以直接上传 |
| 混合检索 | seekdb 全文索引（IK 中文分词）+ 向量索引（bge-small-zh），RRF 融合排序 |
| 类别筛选 | 一级子文件夹自动成为类别，由 seekdb 元数据过滤实现 |
| 定位出处 | 检索结果和待办都给出文件名与段落序号 |
| 纪要待办 | 从已导入 seekdb 的 Markdown 切片中识别 `- [ ]` / `- [x]`，提取任务、负责人和截止日期 |
| 待办筛选 | 按负责人、完成状态和截止日期组合筛选；缺失负责人或日期明确显示“未指定” |
| 状态持久化 | 勾选状态通过后端写入 seekdb，服务重启后仍保留 |
| 幂等同步 | 重复导入不制造重复任务；用户改过的状态不被源文件重新导入覆盖 |
| 增量维护 | 重新索引时更新修改过的文件、删除已不存在的文件及其待办 |

日期只解析任务文本中明确出现的日期：`2026-10-22` 规范化为 `2026-10-22`，`10/22` 保留为 `10/22`，不会猜测年份；无效或缺失日期显示“未指定”。负责人使用“负责人：任务”前缀，例如 `- [ ] 陈浩：消融实验（10/22）`。

## seekdb 在项目中的作用

本项目选用 **seekdb**，承担资料切片、待办及用户修改状态的本地存储与查询，代码集中在 [`zhiku/store.py`](zhiku/store.py) 和 [`zhiku/todos.py`](zhiku/todos.py)。

| 集合 / 环节 | seekdb 能力 | 代码 |
| --- | --- | --- |
| `zhiku_chunks` 存储 | 嵌入式 seekdb，数据保存在本地目录，无需单独部署数据库服务 | `pyseekdb.Client(path=...)` |
| 资料写入 | 写入切片时建立全文索引与向量索引 | `collection.upsert(...)` |
| 混合检索 | 全文（IK 分词）+ 向量 + RRF 融合 | `collection.hybrid_search(...)` |
| 类别筛选 | 元数据条件过滤 | `where={"tag": {"$eq": "科研"}}` |
| `zhiku_todos` 待办 | 无向量集合存储任务元数据、来源状态和用户覆盖状态 | `TodoStore.sync_source(...)` |
| 待办查询与修改 | 分页读取、元数据筛选、完整记录更新 | `TodoStore.query(...)` / `set_done(...)` |
| 来源维护 | 按来源文件删除旧切片和对应待办 | `collection.delete(where={"source": ...})` |

```python
col.hybrid_search(
    query={"where_document": {"$contains": "发票 交给财务"}},
    knn={"query_texts": ["发票什么时候要交给财务"]},
    rank={"rrf": {}},
    n_results=8,
)
```

待办不是网页直接扫描 Markdown 文件得到的：导入切片写回 seekdb 后，后端再读取对应来源的切片，解析并同步到 `zhiku_todos`。前端只通过 `GET /api/todos` 和 `PATCH /api/todos/{todo_id}` 读取、修改任务。

## 运行说明

### 运行环境

| 项目 | 要求 |
| --- | --- |
| 操作系统 | 当前嵌入式模式使用 Linux x86_64；Windows 请使用 WSL2，本轮在 Ubuntu 24.04 验证 |
| Python | 3.11 及以上；本轮 WSL 集成验证使用 3.12.3 |
| 数据目录 | Windows WSL2 下必须放在 Linux 原生文件系统，例如 `$HOME/.zhiku_data` 或 `/tmp/zhiku-data`，不要放在 `/mnt/d` |
| 网络 | 首次运行需要下载约 95MB 的向量模型；之后可离线使用 |
| seekdb | 随 `pyseekdb` 安装，不需要另外部署数据库服务或 Docker |

依赖及版本见 [requirements.txt](requirements.txt)：pyseekdb 1.4.0.post1、FastAPI 0.162.0、uvicorn 0.54.0、python-multipart 0.0.32、pypdf 6.17.0、pytest 9.2.0。

### 从安装到启动（Linux / Windows WSL2）

```bash
# 1. 获取代码
git clone -b 2026-competition-ecosystem https://github.com/Luchutong/powercontext.git
cd powercontext/qoder

# 2. 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. 使用 Linux 原生目录保存 seekdb 数据
export ZHIKU_DB="$HOME/.zhiku_data"

# 4. 导入示例资料
python -m zhiku ingest ./sample_docs

# 5. 启动网页界面，浏览器打开 http://127.0.0.1:8000
python -m zhiku serve --docs ./sample_docs
```

命令行也可以直接检索：`python -m zhiku search "发票什么时候要交给财务"`。用自己的资料时，把 `./sample_docs` 换成你的文件夹，子文件夹名会成为类别。

### 可选配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ZHIKU_DB` | `./zhiku_data` | seekdb 数据目录；WSL 中建议按上文显式设为 Linux 原生路径 |
| `ZHIKU_EMBEDDING` | `bge-zh` | `bge-zh` 本地中文模型；`qwen` 阿里云百炼 text-embedding-v4（需 `DASHSCOPE_API_KEY`）；`default` pyseekdb 自带英文模型 |
| `HF_ENDPOINT` | `https://hf-mirror.com` | 模型下载地址；在海外可设为 `https://huggingface.co` |

切换 `ZHIKU_EMBEDDING` 后向量维度会变化，需要使用新的数据目录重新导入。

## 验证样例

**初始数据**：[`sample_docs/`](sample_docs/) 下 6 份虚构示例资料，共 7 个切片、3 项纪要待办。

### 一键端到端验证

在 `qoder/` 目录运行：

```bash
python -m zhiku verify
```

该命令实际调用 seekdb，并把全文、向量、混合检索结果及待办验证结果写入 [docs/verify-log.md](docs/verify-log.md)。2026-10-30 的记录中，以下 **7 个检索样例和 4 项待办检查全部 PASS**。

| # | 输入（问题） | 预期结果（混合检索前 3 名内） |
| --- | --- | --- |
| 1 | 发票什么时候要交给财务 | `行政/报销与差旅规定.md`：问法不同也能命中“当月 25 日前提交给财务” |
| 2 | 实验二的消融分析谁负责，什么时候截止 | `科研/组会纪要-1015.md` |
| 3 | RAG 里混合检索为什么比纯向量好 | `科研/论文笔记-RAG混合检索.md` |
| 4 | 项目A 需求评审有哪些遗留问题 | `工作/项目A-需求评审.md` |
| 5 | B+ 树和 LSM 树的区别 | `学习/数据库课程笔记.md` |
| 6 | 出差住宿标准是多少 | `行政/报销与差旅规定.md` |
| 7 | 谁负责（类别筛选：科研） | `科研/组会纪要-1015.md`，且结果全部属于“科研” |

待办端到端验证覆盖：

1. 初次提取、缺失字段及来源位置；
2. 修改状态后重新导入，不重复且保留用户状态；
3. 新建 `Store` 后仍能读取持久化状态；
4. 负责人、状态和截止日期组合筛选。

### 浏览器手动验证

启动服务后可检查：

- 搜索“发票什么时候要交给财务”，第一条为《报销与差旅规定》并高亮关键词；
- 选择“科研”后搜索“谁负责”，结果只包含科研资料；
- 上传 Markdown 后检索新内容，重新索引后删除已移除的来源；
- 在“纪要待办”中组合筛选负责人、状态、日期，勾选任务并刷新页面；
- 重新导入或服务正常重启后，任务不重复，用户修改的完成状态仍保留。

本轮已实际完成上述浏览器检查；待办界面截图见 [docs/qoder-records/02-todos-browser.png](docs/qoder-records/02-todos-browser.png)。

### 自动化测试

```bash
# 项目位于 /mnt/d 等挂载目录时，pytest 临时数据库也要放到 Linux 原生目录
TMPDIR=/tmp python -m pytest
```

本轮 WSL 实际结果为 **7 passed in 14.45s**。测试覆盖切片、关键词处理、检索、任务字段解析、重复导入、两个方向的状态覆盖、持久化、筛选、来源位置和删除来源。Windows 原生环境只运行不需要嵌入式 seekdb 的单元测试，实际结果为 **5 passed, 2 deselected**；这不代表 Windows 原生集成测试通过。

## 初版原型与本轮 Qoder 开发的分工

初版资料检索原型及原展示网站是在本轮之前使用其他工具完成的，本轮没有改写这段历史。

2026-10-30 本轮在 Qoder 中完成的开发包括：读取并复现原型、设计 `zhiku_todos` 数据模型、实现任务提取与 seekdb 持久化、增加后端 API 和网页待办看板、补充自动化与端到端验证、重新生成真实演示数据并更新展示网站。实际证据和仍缺材料见 [docs/qoder-records/](docs/qoder-records/)，不把未保存的对话或终端输出伪称为截图。

- Qoder 账号 UID：尚未填写。

## 项目结构

```text
qoder/
├── zhiku/
│   ├── store.py      # seekdb 资料存储、混合检索与来源维护
│   ├── todos.py      # Markdown 任务解析、seekdb 待办同步与状态持久化
│   ├── ingest.py     # 读取文件、切片、导入
│   ├── app.py        # FastAPI 检索与待办接口
│   ├── verify.py     # 检索与待办端到端验证
│   ├── demo.py       # 导出真实检索和待办演示数据
│   └── __main__.py   # 命令行入口
├── web/index.html    # 本地检索与待办网页
├── site/             # 静态项目网站（Qoder Sites）
├── sample_docs/      # 虚构示例资料
├── tests/            # 自动化测试
└── docs/
    ├── verify-log.md     # 实际运行生成的验证记录
    ├── qoder-records/    # 本轮开发记录与真实截图
    └── QODER_GUIDE.md    # 本轮实施状态和交付步骤
```

## 项目网站

`site/` 是纯静态网站。网站的检索结果和待办表格来自本地真实运行 `python -m zhiku export-demo` 导出的 `site/demo-data.js`，页面已明确标注为预生成数据；静态网站不能运行 Python 或修改 seekdb。勾选、筛选和持久化需要在本地应用中体验。

修改示例资料或检索、待办逻辑后，重新运行以下命令更新演示：

```bash
python -m zhiku export-demo
```

## 第三方资源说明

- [seekdb / pyseekdb](https://github.com/oceanbase/seekdb)（Apache-2.0）：数据库与检索。
- 向量模型 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)（MIT），使用 [Xenova/bge-small-zh-v1.5](https://huggingface.co/Xenova/bge-small-zh-v1.5) 提供的 ONNX 版本，通过 pyseekdb 的 `OnnxEmbeddingFunction` 加载，未做修改。
- `sample_docs/` 中的示例资料为本项目编写的虚构内容。
- 配置示例中的 API Key 均为占位说明，仓库中不包含任何真实凭据。
