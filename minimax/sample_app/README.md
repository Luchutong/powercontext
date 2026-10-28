# taskboard：验证样例使用的业务工程

一个只依赖 Python 标准库的任务看板服务（WSGI），带 pytest 测试。它是 SDD 上下文模块的“被修改对象”：
仓库中提交的是**修改前**版本，`GET /tasks` 还不支持按标签过滤；修改由 MiniMax 在工作副本
`.runtime/workspace/sample_app` 中完成，修改前后快照见 `../snapshots/`。

| 路径 | 内容 |
| --- | --- |
| `taskboard/service.py` | 列表查询 `search_tasks`、创建任务 `create_task`（本次修改目标：`search_tasks`） |
| `taskboard/api.py` | WSGI 路由：`GET /tasks`、`POST /tasks`、`GET /tasks/export.csv` |
| `taskboard/models.py`、`repository.py`、`export.py` | 数据模型、初始数据加载、CSV 导出 |
| `data/tasks.json` | 初始数据（8 条任务） |
| `specs/*.md` | 规格条目：`LIST-*`（列表现有行为）、`TAG-*`（本次变更）、`CRT-*`（创建）、`EXP-*`（导出） |
| `tests/` | `test_list_api.py`（回归）、`test_tag_filter.py`（目标功能）、`test_create_task.py`、`test_export.py` |

测试用 `@pytest.mark.spec("TAG-001")` 标注所验证的规格条目，代码在 docstring 中注明实现的条目，
上下文模块据此在 seekdb 中建立“测试 → 规格”“代码 → 规格”的关联。

```bash
# 在 minimax/ 目录下（需先运行 scripts/install.sh）
cd sample_app
../.venv/bin/python -m pytest -q                      # 修改前：10 个目标用例失败，17 个用例通过
../.venv/bin/python -m taskboard list status=open     # 命令行查看 GET /tasks 的结果
../.venv/bin/python -m taskboard serve --port 8080    # 启动 HTTP 服务：curl 'http://127.0.0.1:8080/tasks?status=open'
```
