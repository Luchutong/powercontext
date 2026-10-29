# 📚 知库 ZhiKu

**把散落各处的组会纪要、论文笔记、周报、规章制度收进一个本地资料库，一句话找到原文。**

- 项目网站：`<发布到 Qoder Sites 后把地址填在这里>`
- 开发工具：Qoder
- 核心依赖：[seekdb](https://github.com/oceanbase/seekdb)（pyseekdb 嵌入式模式）

## 解决什么问题

科研和工作中，真正耗时的往往不是做事，而是"找到之前定下来的那件事"：

- 资料分散在不同文件夹、不同格式里，找一句话要挨个打开；
- 记得意思却记不清原话，Ctrl+F 搜"发票什么时候交"搜不到"当月 25 日前提交给财务"；
- 任务负责人和截止日期写在纪要里，过几天又要重新翻。

知库把这些资料统一导入 seekdb，用「全文 + 语义」混合检索，直接返回最相关的段落和出处。

## 功能

| 功能 | 说明 |
| --- | --- |
| 一键导入 | 读取文件夹里的 Markdown / TXT / PDF，按段落切成约 400 字的片段；网页里也可以直接上传 |
| 混合检索 | seekdb 全文索引（IK 中文分词）+ 向量索引（bge-small-zh），RRF 融合排序 |
| 类别筛选 | 一级子文件夹自动成为类别（科研 / 工作 / 行政 / 学习） |
| 定位出处 | 结果给出文件名和段落序号，并高亮关键词 |
| 增量维护 | 重新索引时自动更新修改过的文件、删除已不存在的文件 |

## seekdb 在项目里怎么用

所有存储和检索都在 [`zhiku/store.py`](zhiku/store.py)：

```python
client = pyseekdb.Client(path="./zhiku_data")              # 嵌入式 seekdb，数据就是本地目录
col = client.get_or_create_collection("zhiku_chunks", embedding_function=bge_small_zh)

col.upsert(ids=..., documents=..., metadatas=...)            # 写入切片，自动建全文 + 向量索引

col.hybrid_search(
    query={"where_document": {"$contains": "发票 交给财务"}},  # 全文检索（IK 分词）
    knn={"query_texts": ["发票什么时候要交给财务"]},           # 向量检索（语义）
    rank={"rrf": {}},                                         # RRF 融合排序
    n_results=8,
)
```

- 按类别筛选用的是 seekdb 的元数据过滤：`where={"tag": {"$eq": "科研"}}`
- 删除/更新某个文件的切片：`col.delete(where={"source": {"$eq": ...}})`

## 快速开始

需要 Python 3.11+（macOS 15+ / Linux / Windows）。

```bash
pip install -r requirements.txt

# 导入示例资料（首次运行会下载约 95MB 的中文向量模型）
python -m zhiku ingest ./sample_docs

# 命令行检索
python -m zhiku search "发票什么时候要交给财务"

# 网页界面：http://127.0.0.1:8000
python -m zhiku serve --docs ./sample_docs
```

用自己的资料：把 `./sample_docs` 换成你的文件夹。

### 可选配置（环境变量）

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `ZHIKU_DB` | `./zhiku_data` | seekdb 数据目录 |
| `ZHIKU_EMBEDDING` | `bge-zh` | `bge-zh` 本地中文模型；`qwen` 阿里云百炼 text-embedding-v4（需 `DASHSCOPE_API_KEY`）；`default` 英文模型 |
| `HF_ENDPOINT` | `https://hf-mirror.com` | 模型下载地址；在海外可设为 `https://huggingface.co` |

> 切换 `ZHIKU_EMBEDDING` 后向量维度会变化，需要删除 `zhiku_data` 目录重新导入。

## 项目结构

```text
zhiku/
├── store.py      # seekdb 存储与混合检索
├── ingest.py     # 读取文件、切片、导入
├── app.py        # FastAPI 接口
├── demo.py       # 导出网站演示数据
└── __main__.py   # 命令行
web/index.html    # 本地网页界面
site/             # 项目网站（通过 Qoder Sites 发布）
sample_docs/      # 示例资料（内容均为虚构）
tests/            # 测试：python -m pytest
docs/QODER_GUIDE.md  # 用 Qoder 继续开发和发布的步骤
```

## 项目网站

`site/` 是一个纯静态网站，通过 Qoder Sites 发布。网站上的"在线体验"展示的是本地运行
`python -m zhiku export-demo` 时 seekdb 返回的真实检索结果（写在 `site/demo-data.js`）。
修改示例资料或检索逻辑后，重新运行这条命令即可更新网站演示。

## 测试

```bash
python -m pytest            # 全部测试（含 seekdb 集成测试）
python -m pytest -m "not slow"  # 只跑不依赖模型下载的单元测试
```
