# 本轮 Qoder 实际开发与交付指南

本文记录 2026-10-30 这一轮已经实际完成的开发和验证，不把计划中的功能写成已实现，也不把初版原型的开发历史改写成 Qoder 完成。

## 1. 范围与真实分工

- 独立开发目录：`powercontext-qoder/`，只修改其中的 `qoder/`。
- 分支：`2026-competition-ecosystem`。
- 开发起点：`0f239d35430ded99f35705f9df226b5ae25917d1`。
- 初版资料检索核心和原展示网站在本轮之前由其他工具完成。
- 本轮在 Qoder 中完成纪要待办的后端、界面、测试、真实浏览器验证、文档和网站更新。

本轮没有实现“使用记录”或“AI 总结回答”，README 和网站不声称具备这些功能。

## 2. 原型复现与环境结论

本轮先阅读 README、原开发指南和 `zhiku/` 源码，再创建隔离环境并运行检索原型。实际确认：

- Windows 原生嵌入式模式缺少 `pylibseekdb`，不能据此声称 Windows 集成验证通过。
- WSL Ubuntu 24.04 / Python 3.12.3 可以运行嵌入式 seekdb。
- 项目位于 `/mnt/d` 时，seekdb 数据目录若也在挂载盘会出现 `seekdb_open failed: code=-1`；改用 `/tmp` 等 Linux 原生目录后正常运行。
- pytest 在挂载盘运行时需同时设置 `TMPDIR=/tmp`，避免临时数据库落到 `/mnt/d`。

复现及后续集成验证使用独立 Linux 虚拟环境和 Linux 原生数据库目录，例如：

```bash
export ZHIKU_DB=/tmp/zhiku-qoder-baseline
.venv-linux/bin/python -m zhiku verify
TMPDIR=/tmp .venv-linux/bin/python -m pytest
```

## 3. 本轮已实现的待办功能

| 层 | 实际实现 |
| --- | --- |
| 解析与存储 | 新建 `zhiku/todos.py` 和 `zhiku_todos` 集合，从 seekdb 切片解析 Markdown `- [ ]` / `- [x]`，保存任务、负责人、日期、来源、来源状态和用户覆盖状态 |
| 同步 | 以来源和规范化任务正文生成稳定 ID；重复导入不重复，未修改任务跟随源复选框，用户修改状态在重导入后保留 |
| 资料维护 | 上传、替换、重新索引后同步任务；删除来源同时删除对应切片和任务 |
| API | `GET /api/todos` 支持负责人、状态、日期组合筛选；`PATCH /api/todos/{todo_id}` 修改完成状态，不存在的任务返回 404 |
| 网页 | 新增“纪要待办”面板、筛选器、任务复选框、来源位置、空状态和保存失败提示；上传及重索引后刷新待办 |
| 并发 | 资料与待办存储共享 `threading.RLock`，避免同一后端进程内互相干扰 |

无向量集合不能直接写入只有 document 的记录，因此待办正文存放在 metadata 中，upsert 使用 IDs + metadata。这是实际调试后采用的实现，不是伪造的 seekdb 调用。

## 4. 已实际运行的自动化验证

### 检索与待办端到端验证

```bash
.venv-linux/bin/python -m zhiku verify
```

2026-10-30 22:51:50 的实际结果：7 个检索样例 PASS，以下 4 个待办检查 PASS：

1. 初次提取、缺失字段及出处；
2. 修改状态并重新导入，不重复且保留状态；
3. 新建 `Store` 后读取持久化状态；
4. 负责人 + 状态 + 截止日期组合筛选。

完整三路检索结果和任务结果见 [`verify-log.md`](verify-log.md)。该文件由命令生成，不是手工编造的 PASS 记录。

### pytest

```bash
TMPDIR=/tmp .venv-linux/bin/python -m pytest
```

WSL 实际结果：`7 passed in 14.45s`。Windows 原生仅运行纯单元测试时为 `5 passed, 2 deselected`，未把它写成完整集成测试通过。

## 5. 已实际完成的浏览器检查

本地服务使用 `/tmp/zhiku-qoder-baseline`，检查了：

- 原有混合检索和科研类别筛选；
- Markdown 上传、检索新内容、重索引删除来源；
- 三条示例待办及“未指定”字段展示；
- 负责人、状态、日期筛选；
- 勾选后刷新、重复导入及服务正常 `SIGTERM` 重启后的状态保留；
- 不存在的待办 PATCH 返回 404，非法状态返回 422。

真实待办界面截图：[`qoder-records/02-todos-browser.png`](qoder-records/02-todos-browser.png)。Qoder 对话和终端截图没有保存的不补造，缺口在开发记录中明确列出。

## 6. 静态网站与真实演示数据

`site/` 不能运行 Python 或 seekdb。以下命令从真实本地数据库重新生成了检索结果和待办数据：

```bash
.venv-linux/bin/python -m zhiku export-demo
```

输出为 `site/demo-data.js`。网站已明确标注“预生成数据，静态展示，不会修改数据库”，不能把网站上的待办表格描述为在线持久化体验。

本地浏览器已检查 442px 手机宽度、检索问题切换、待办表格和三张截图资源；页面无横向溢出，表格在自身容器内滚动，控制台无错误。

2026-10-30 已通过 Qoder Sites 发布，可见范围回读为 `public`：

- 网址：<https://zhiku-seekdb-db1ghqb6plk.qoder.zone>
- 独立无痕 Chrome 会话直接打开该网址，页面标题、6 个检索按钮和 3 条待办均正常加载；无凭据 `curl` 请求返回 HTTP 200。
- 发布截图：[`qoder-records/04-site-public.png`](qoder-records/04-site-public.png)。截图证明发布页面内容，公开权限由 Sites 回读和无痕访问共同验证。

## 7. Commit 与提交边界

第一次提交曾因 Git 作者身份未配置而失败。随后使用 WSL 中已登录的 GitHub 账户 `Luchutong` 的公开姓名和邮箱，通过命令级 `git -c user.name=... -c user.email=...` 创建提交，没有修改 Git 配置。

已创建的开发提交：

| 阶段 | Commit |
| --- | --- |
| seekdb 待办后端 | `2bb385091ae6dfa9b3391b1f63d82fef8982bebd` |
| 待办界面、测试与测试配置 | `3d8293b798ab6aaf8b8d80e94df36ae5eb5525f9` |
| 端到端验证与生成日志 | `19e38e37c4124485aeabd4ef8a416147476490f2` |

提测必须填写最终推送后 `2026-competition-ecosystem` 分支的 HEAD，而不是开发起点或上述中间提交。只提交 `qoder/` 内资产；推送前再次获取远程分支并确认没有非预期分叉，不使用强制推送。

## 8. 当前交付清单

- [x] 独立目录与起点 Commit 已记录。
- [x] 原型检索、上传、类别筛选已复现并进行浏览器验证。
- [x] 待办提取、筛选、状态持久化、幂等重导入已实现。
- [x] 7 个检索样例、4 个待办端到端检查和 7 个 pytest 测试实际通过。
- [x] README、验证日志、真实演示数据和网站功能说明已按实现更新。
- [x] Qoder Sites 已发布，公开访问及无痕 Chrome 验证已完成。
- [x] 本地分阶段提交已创建；提测 Commit 以最终推送 HEAD 为准。
- [ ] Qoder 账号 UID 仍待填写。
- [ ] Qoder 对话 / 终端截图仍缺，不能用应用截图替代。
- [ ] 使用效果没有真实数据，保持空缺。
