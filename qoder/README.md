# 📚 知库 ZhiKu

**把散落各处的组会纪要、论文笔记、周报、规章制度收进一个本地资料库，一句话找到原文。**

| 提交材料 | 位置 |
| --- | --- |
| 项目网站（Qoder Sites） | `<发布后填写完整网址>` |
| 代码仓库 | <https://github.com/Luchutong/powercontext/tree/2026-competition-ecosystem/qoder> |
| 运行说明 | 本文 [运行说明](#运行说明) |
| 验证样例 | 本文 [验证样例](#验证样例)，检索记录见 [docs/verify-log.md](docs/verify-log.md) |
| Qoder 开发记录 | [docs/qoder-records/](docs/qoder-records/)，Qoder 账号 UID：`<填写>` |
| 使用效果（鼓励） | 本文 [使用效果](#使用效果) |

## 解决什么问题

科研和工作中，真正耗时的往往不是做事，而是"找到之前定下来的那件事"：

- 资料分散在不同文件夹、不同格式里，找一句话要挨个打开；
- 记得意思却记不清原话，Ctrl+F 搜"发票什么时候交"搜不到"当月 25 日前提交给财务"；
- 任务负责人和截止日期写在纪要里，过几天又要重新翻。

知库把这些资料统一导入 seekdb，用「全文 + 语义」混合检索，直接返回最相关的段落和出处。

## 核心功能

| 功能 | 说明 |
| --- | --- |
| 一键导入 | 读取文件夹里的 Markdown / TXT / PDF，按段落切成约 400 字的片段；网页里也可以直接上传 |
| 混合检索 | seekdb 全文索引（IK 中文分词）+ 向量索引（bge-small-zh），RRF 融合排序 |
| 类别筛选 | 一级子文件夹自动成为类别（科研 / 工作 / 行政 / 学习），由 seekdb 元数据过滤实现 |
| 定位出处 | 结果给出文件名和段落序号，并高亮关键词 |
| 增量维护 | 重新索引时自动更新修改过的文件、删除已不存在的文件 |

## seekdb 在项目中的作用

本项目选用 **seekdb**，承担全部数据存储与检索，代码集中在 [`zhiku/store.py`](zhiku/store.py)：

| 环节 | seekdb 能力 | 代码 |
| --- | --- | --- |
| 存储 | 嵌入式 seekdb，数据保存在本地目录 `zhiku_data/`，无需单独部署数据库 | `pyseekdb.Client(path=...)` |
| 写入 | 写入切片时自动建立全文索引与向量索引 | `collection.upsert(...)` |
| 检索 | 全文（IK 分词）+ 向量 + RRF 融合的混合检索 | `collection.hybrid_search(...)` |
| 筛选 | 元数据条件过滤（按类别） | `where={"tag": {"$eq": "科研"}}` |
| 维护 | 按来源文件删除旧切片 | `collection.delete(where={"source": ...})` |

```python
col.hybrid_search(
    query={"where_document": {"$contains": "发票 交给财务"}},  # 全文检索（IK 分词）
    knn={"query_texts": ["发票什么时候要交给财务"]},           # 向量检索（语义）
    rank={"rrf": {}},                                         # RRF 融合排序
    n_results=8,
)
```

## 运行说明

### 运行环境

| 项目 | 要求 |
| --- | --- |
| 操作系统 | macOS 15+ / Linux（glibc 2.28+）/ Windows，x86_64 或 arm64 |
| Python | 3.11 及以上（开发与验证使用 3.13） |
| 网络 | 首次运行需要下载约 95MB 的向量模型；之后可离线使用 |
| seekdb | 嵌入式模式，随 `pyseekdb` 一起安装，**不需要**另外部署数据库服务或 Docker |

依赖及版本见 [requirements.txt](requirements.txt)：pyseekdb 1.4.0.post1、FastAPI 0.162.0、uvicorn 0.54.0、python-multipart 0.0.32、pypdf 6.17.0、pytest 9.2.0。

### 从安装到启动

```bash
# 1. 获取代码
git clone -b 2026-competition-ecosystem https://github.com/Luchutong/powercontext.git
cd powercontext/qoder

# 2. 创建虚拟环境并安装依赖
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. 导入示例资料到 seekdb
python -m zhiku ingest ./sample_docs

# 4. 启动网页界面，浏览器打开 http://127.0.0.1:8000
python -m zhiku serve --docs ./sample_docs
```

命令行也可以直接检索：`python -m zhiku search "发票什么时候要交给财务"`。
用自己的资料：把 `./sample_docs` 换成你的文件夹，子文件夹名会成为类别。

### 可选配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ZHIKU_DB` | `./zhiku_data` | seekdb 数据目录 |
| `ZHIKU_EMBEDDING` | `bge-zh` | `bge-zh` 本地中文模型；`qwen` 阿里云百炼 text-embedding-v4（需 `DASHSCOPE_API_KEY`）；`default` pyseekdb 自带英文模型 |
| `HF_ENDPOINT` | `https://hf-mirror.com` | 模型下载地址；在海外可设为 `https://huggingface.co` |

切换 `ZHIKU_EMBEDDING` 后向量维度会变化，需要删除 `zhiku_data/` 后重新导入。

## 验证样例

**初始数据**：[`sample_docs/`](sample_docs/) 下 6 份示例资料（科研 / 工作 / 行政 / 学习四类，内容均为虚构）。

**一键验证**（在 `qoder/` 目录下）：

```bash
python -m zhiku verify
```

该命令会导入示例资料，依次执行下表中的问题，在终端输出 PASS / FAIL，并把每个问题在 seekdb **全文检索、向量检索、混合检索**下的结果写入 [docs/verify-log.md](docs/verify-log.md)，作为 seekdb 发挥作用的记录。

| # | 输入（问题） | 预期结果（混合检索前 3 名内） |
| --- | --- | --- |
| 1 | 发票什么时候要交给财务 | `行政/报销与差旅规定.md`：原文是"当月 25 日前提交给财务"，问法不同也能命中 |
| 2 | 实验二的消融分析谁负责，什么时候截止 | `科研/组会纪要-1015.md`：陈浩负责，10 月 22 日前 |
| 3 | RAG 里混合检索为什么比纯向量好 | `科研/论文笔记-RAG混合检索.md` |
| 4 | 项目A 需求评审有哪些遗留问题 | `工作/项目A-需求评审.md` |
| 5 | B+ 树和 LSM 树的区别 | `学习/数据库课程笔记.md` |
| 6 | 出差住宿标准是多少 | `行政/报销与差旅规定.md` |
| 7 | 谁负责（类别筛选：科研） | `科研/组会纪要-1015.md`，且结果全部属于"科研"。不加筛选时排第一的是工作类的《项目A-需求评审》，可见筛选生效 |

**在网页界面手动验证**：启动服务后，在搜索框输入第 1 个问题，第一条结果应为《报销与差旅规定》，"发票""交给财务"被高亮；点击"科研"标签后搜索"谁负责"，结果只包含科研类资料。

**自动化测试**：`python -m pytest`（包含切片、关键词处理和 seekdb 导入/检索/删除的集成测试）。

## Qoder 开发记录

项目在 Qoder 中开发，关键环节的对话截图与说明见 [docs/qoder-records/](docs/qoder-records/)。

- Qoder 账号 UID：`<填写>`

## 使用效果

`<鼓励提交：使用人数、使用次数、使用前后对比（如找一条规定从 X 分钟缩短到 Y 秒）、用户反馈；附访问统计截图、使用日志或问卷结果。隐去姓名和联系方式。>`

## 项目结构

```text
qoder/
├── zhiku/
│   ├── store.py      # seekdb 存储与混合检索
│   ├── ingest.py     # 读取文件、切片、导入
│   ├── app.py        # FastAPI 接口
│   ├── verify.py     # 验证样例与检索记录
│   ├── demo.py       # 导出网站演示数据
│   └── __main__.py   # 命令行入口
├── web/index.html    # 本地网页界面
├── site/             # 项目网站（通过 Qoder Sites 发布）
├── sample_docs/      # 示例资料（内容均为虚构）
├── tests/            # 自动化测试
└── docs/
    ├── verify-log.md     # 验证样例生成的 seekdb 检索记录
    ├── qoder-records/    # Qoder 开发记录截图
    └── QODER_GUIDE.md    # 开发、发布与提交步骤
```

## 项目网站

`site/` 是纯静态网站，通过 Qoder Sites 发布。网站上的"在线体验"展示的是本地运行
`python -m zhiku export-demo` 时 seekdb 返回的真实检索结果（保存在 `site/demo-data.js`），
截图在 `site/assets/`。修改示例资料或检索逻辑后，重新运行这条命令即可更新网站演示。

## 第三方资源说明

- [seekdb / pyseekdb](https://github.com/oceanbase/seekdb)（Apache-2.0）：数据库与检索。
- 向量模型 [BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)（MIT），使用 [Xenova/bge-small-zh-v1.5](https://huggingface.co/Xenova/bge-small-zh-v1.5) 提供的 ONNX 版本，通过 pyseekdb 的 `OnnxEmbeddingFunction` 加载，未做修改。
- `sample_docs/` 中的示例资料为本项目编写的虚构内容。
- 配置示例中的 API Key 均为占位说明，仓库中不包含任何真实凭据。
