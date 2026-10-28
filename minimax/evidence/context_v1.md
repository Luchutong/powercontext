# 开发上下文：TASK-TAG-FILTER 任务列表接口支持按标签过滤

- 生成时间：2026-10-28T04:41:29Z
- 资料版本：seekdb import #1（spec_digest `84e4b299421f`）
- 检索引擎：5.7.25-OceanBase seekdb-v1.4.0.0（embedded，库 `sdd_context`）
- 选中组件：GET /tasks

## 适用规格条目

| 编号 | 类型 | 标题 | 来源 | rev | 本次导入变化 | 测试 |
| --- | --- | --- | --- | --- | --- | --- |
| TAG-001 | 变更 | 按标签过滤任务 | `specs/tag-filter.md#L9-L13` | 1 | 否 | 3 |
| TAG-002 | 变更 | 标签匹配规则 | `specs/tag-filter.md#L15-L19` | 1 | 否 | 1 |
| TAG-003 | 变更 | 与现有过滤、排序和分页组合 | `specs/tag-filter.md#L21-L25` | 1 | 否 | 2 |
| TAG-004 | 变更 | 空标签参数 | `specs/tag-filter.md#L27-L31` | 1 | 否 | 2 |
| LIST-001 | 保持 | 默认排序 | `specs/tasks-list.md#L9-L13` | 1 | 否 | 1 |
| LIST-002 | 保持 | 状态过滤 | `specs/tasks-list.md#L15-L19` | 1 | 否 | 2 |
| LIST-003 | 保持 | 数量限制 | `specs/tasks-list.md#L21-L25` | 1 | 否 | 2 |
| LIST-004 | 保持 | 响应结构 | `specs/tasks-list.md#L27-L31` | 1 | 否 | 2 |
| LIST-005 | 保持 | 未知查询参数 | `specs/tasks-list.md#L33-L37` | 1 | 否 | 1 |

## 需要修改的代码文件与符号

| 角色 | 符号 | 位置 | 理由 |
| --- | --- | --- | --- |
| 修改 | `taskboard/service.py::search_tasks` | `taskboard/service.py#L35-L52` | 被 5 个目标测试直接调用/引用；实现规格 LIST-001, LIST-002, LIST-003, LIST-004, LIST-005；与变更条目全文相关度 1.0 |
| 相关 | `taskboard/service.py::QueryError` | `taskboard/service.py#L14-L15` | 被 1 个目标测试直接调用/引用；实现规格 LIST-002, LIST-003；与变更条目全文相关度 0.6986 |
| 相关 | `taskboard/api.py::list_tasks_view` | `taskboard/api.py#L36-L42` | 实现规格 LIST-002, LIST-003, LIST-004；与变更条目全文相关度 0.5225 |
| 相关 | `taskboard/service.py::_parse_limit` | `taskboard/service.py#L22-L32` | 实现规格 LIST-003；与变更条目全文相关度 0.5049 |

## 验证测试

| 用途 | 测试 | 位置 | 验证规格 |
| --- | --- | --- | --- |
| 目标（功能） | `tests/test_tag_filter.py::test_blank_tag_is_rejected` | `tests/test_tag_filter.py#L48-L52` | TAG-004 |
| 目标（功能） | `tests/test_tag_filter.py::test_blank_tag_returns_400_over_http` | `tests/test_tag_filter.py#L55-L59` | TAG-004 |
| 目标（功能） | `tests/test_tag_filter.py::test_get_tasks_with_tag_over_http` | `tests/test_tag_filter.py#L40-L45` | TAG-001, TAG-003 |
| 目标（功能） | `tests/test_tag_filter.py::test_search_tasks_returns_only_tasks_with_tag` | `tests/test_tag_filter.py#L12-L17` | TAG-001 |
| 目标（功能） | `tests/test_tag_filter.py::test_tag_combines_with_status_and_keeps_order_limit_and_total` | `tests/test_tag_filter.py#L33-L37` | TAG-003 |
| 目标（功能） | `tests/test_tag_filter.py::test_tag_match_ignores_case_and_surrounding_whitespace` | `tests/test_tag_filter.py#L26-L30` | TAG-002 |
| 目标（功能） | `tests/test_tag_filter.py::test_unknown_tag_returns_empty_result` | `tests/test_tag_filter.py#L20-L23` | TAG-001 |
| 回归（兼容） | `tests/test_list_api.py::test_default_listing_sorted_by_priority_then_id` | `tests/test_list_api.py#L12-L16` | LIST-001 |
| 回归（兼容） | `tests/test_list_api.py::test_invalid_limit_raises_query_error` | `tests/test_list_api.py#L40-L44` | LIST-003 |
| 回归（兼容） | `tests/test_list_api.py::test_invalid_status_is_rejected_with_400` | `tests/test_list_api.py#L26-L30` | LIST-002 |
| 回归（兼容） | `tests/test_list_api.py::test_limit_truncates_items_but_not_total` | `tests/test_list_api.py#L33-L37` | LIST-003, LIST-004 |
| 回归（兼容） | `tests/test_list_api.py::test_response_shape_over_http` | `tests/test_list_api.py#L47-L54` | LIST-004 |
| 回归（兼容） | `tests/test_list_api.py::test_status_filter_returns_matching_tasks` | `tests/test_list_api.py#L19-L23` | LIST-002 |
| 回归（兼容） | `tests/test_list_api.py::test_unknown_query_parameters_are_ignored` | `tests/test_list_api.py#L57-L61` | LIST-005 |

运行命令（在样例工程根目录）：

- target: `python -m pytest tests/test_tag_filter.py::test_blank_tag_is_rejected tests/test_tag_filter.py::test_blank_tag_returns_400_over_http tests/test_tag_filter.py::test_get_tasks_with_tag_over_http tests/test_tag_filter.py::test_search_tasks_returns_only_tasks_with_tag tests/test_tag_filter.py::test_tag_combines_with_status_and_keeps_order_limit_and_total tests/test_tag_filter.py::test_tag_match_ignores_case_and_surrounding_whitespace tests/test_tag_filter.py::test_unknown_tag_returns_empty_result`
- regression: `python -m pytest tests/test_list_api.py::test_default_listing_sorted_by_priority_then_id tests/test_list_api.py::test_invalid_limit_raises_query_error tests/test_list_api.py::test_invalid_status_is_rejected_with_400 tests/test_list_api.py::test_limit_truncates_items_but_not_total tests/test_list_api.py::test_response_shape_over_http tests/test_list_api.py::test_status_filter_returns_matching_tasks tests/test_list_api.py::test_unknown_query_parameters_are_ignored`
- full: `python -m pytest`

## 任务状态（PowerContext）

- 状态：empty；Scope：`None`
- 已完成：无
- 待办：[baseline] 运行修改前的目标测试与回归测试并记录基线；[edit] 把上下文交给 MiniMax 修改代码；[verify] 运行修改后的目标测试、回归测试和全量测试

## seekdb 查询

| 查询 | 用途 | 行数 |
| --- | --- | --- |
| Q0 | 最近一次导入批次 | 1 |
| Q1 | 规格条目全文检索（任务描述，ik 分词） | 14 |
| Q2 | 命中组件下的全部有效规格条目（变更 + 必须保持） | 9 |
| Q3 | 验证这些规格条目的测试（pytest spec 标记） | 16 |
| Q4 | 实现这些规格条目的代码符号（docstring/注释中的规格编号） | 11 |
| Q5 | 目标测试直接调用/引用的代码符号 | 6 |
| Q6 | 代码符号全文检索（变更条目正文，ik 分词） | 15 |
| Q7 | 修改目标实现的规格条目（影响分析） | 5 |
| Q8 | 选中规格条目的修订历史 | 9 |
| Q9 | 上下文引用的源文件（路径、摘要、行数） | 6 |

完整 SQL 与返回行见 `/workspace/minimax/evidence/seekdb_queries.log`。

## 规格正文

### TAG-001 按标签过滤任务

来源：`specs/tag-filter.md#L9-L13`（sha256 `b0f1cb106241`）

`GET /tasks` 新增查询参数 `tag`。提供该参数时，只返回 `tags` 中包含该标签的任务；没有任务匹配时返回 `{"items": [], "total": 0}`。

### TAG-002 标签匹配规则

来源：`specs/tag-filter.md#L15-L19`（sha256 `65bba3cacb12`）

`tag` 参数值先去除首尾空白，再与任务标签进行不区分大小写的比较。例如 `?tag=%20Backend%20` 与标签 `backend` 匹配。

### TAG-003 与现有过滤、排序和分页组合

来源：`specs/tag-filter.md#L21-L25`（sha256 `f780a1ef2c6e`）

`tag` 与 `status` 同时提供时取交集。排序规则（LIST-001）与 `limit` 规则（LIST-003）不变；`total` 是应用全部过滤条件之后、`limit` 截断之前的数量。

### TAG-004 空标签参数

来源：`specs/tag-filter.md#L27-L31`（sha256 `8f6e45ed583e`）

`tag` 参数存在但为空字符串或只包含空白时视为非法查询：`search_tasks` 抛出 `QueryError`，接口返回 HTTP 400，错误信息包含 `tag`。

### LIST-001 默认排序

来源：`specs/tasks-list.md#L9-L13`（sha256 `2997209d18c7`）

`GET /tasks` 返回的任务按优先级 high → medium → low 排序；同一优先级内按 `id` 升序。

### LIST-002 状态过滤

来源：`specs/tasks-list.md#L15-L19`（sha256 `2e1325df42a2`）

查询参数 `status` 取值为 `open`、`in_progress`、`done` 之一时，只返回该状态的任务；其他取值（包括空字符串）返回 HTTP 400，错误信息包含 `status`。

### LIST-003 数量限制

来源：`specs/tasks-list.md#L21-L25`（sha256 `d469b027ddce`）

查询参数 `limit` 为 1–100 的整数时，最多返回 `limit` 条任务；非整数或超出范围返回 HTTP 400，错误信息包含 `limit`。

### LIST-004 响应结构

来源：`specs/tasks-list.md#L27-L31`（sha256 `7cb1f05d37dd`）

响应体为 `{"items": [...], "total": n}`。`items` 中每项依次包含 `id`、`title`、`status`、`priority`、`tags`；`total` 是应用过滤条件之后、`limit` 截断之前的任务数量。

### LIST-005 未知查询参数

来源：`specs/tasks-list.md#L33-L37`（sha256 `9d5b4ed01cc5`）

接口不认识的查询参数（例如 `sort`、`page`）被忽略，不影响结果，也不返回错误，以兼容旧客户端。
