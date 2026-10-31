# 任务列表接口（GET /tasks）现有行为

- 组件: GET /tasks
- 版本: v1
- 状态: 生效

本文件描述任务列表接口已经上线的行为。任何对该接口的修改都必须保持以下条目。

## LIST-001 默认排序
- 类型: 保持
- 关键词: 排序, 优先级, priority, id, sort

`GET /tasks` 返回的任务按优先级 high → medium → low 排序；同一优先级内按 `id` 升序。

## LIST-002 状态过滤
- 类型: 保持
- 关键词: status, 状态, 过滤, 查询参数, 400

查询参数 `status` 取值为 `open`、`in_progress`、`done` 之一时，只返回该状态的任务；其他取值（包括空字符串）返回 HTTP 400，错误信息包含 `status`。

## LIST-003 数量限制
- 类型: 保持
- 关键词: limit, 分页, 数量, 查询参数, 400

查询参数 `limit` 为 1–100 的整数时，最多返回 `limit` 条任务；非整数或超出范围返回 HTTP 400，错误信息包含 `limit`。

## LIST-004 响应结构
- 类型: 保持
- 关键词: 响应, JSON, items, total, 字段

响应体为 `{"items": [...], "total": n}`。`items` 中每项依次包含 `id`、`title`、`status`、`priority`、`tags`；`total` 是应用过滤条件之后、`limit` 截断之前的任务数量。

## LIST-005 未知查询参数
- 类型: 保持
- 关键词: 兼容, 未知参数, 查询参数, 忽略

接口不认识的查询参数（例如 `sort`、`page`）被忽略，不影响结果，也不返回错误，以兼容旧客户端。
