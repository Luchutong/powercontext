# 任务导出（GET /tasks/export.csv）

- 组件: GET /tasks/export.csv
- 版本: v1
- 状态: 生效

## EXP-001 CSV 列与顺序
- 类型: 保持
- 关键词: csv, 导出, 列, 顺序

导出文件第一行为表头 `id,title,status,priority,tags`，随后按 `id` 升序输出全部任务。

## EXP-002 标签列格式
- 类型: 保持
- 关键词: csv, 导出, tags, 分号

`tags` 列使用分号 `;` 连接多个标签；没有标签时该列为空。
