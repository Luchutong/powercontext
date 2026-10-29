# 用 Qoder 完成开发、发布和提交：一步步操作指南

## 先看清赛题要求（摘自官方题面）

- **代码位置**：基于 `2026-competition-ecosystem` 分支开发，项目资产放在 `qoder/` 目录。提测时 Branch 填这个分支，Commit 填这个分支上的提交。
- **主要开发在 Qoder 中完成**，桌面端、IDE 插件、CLI 都可以。要保留开发过程记录，说明 Qoder 在需求拆解、编码、调试中做了什么。题面原话："仅用 Qoder 发布网站而项目在其他工具中开发的作品，不符合本赛道要求。"
- **seekdb 或 PowerContext 至少一种实际发挥作用**。本项目用的是 seekdb，`python -m zhiku verify` 生成的检索记录就是证明。
- **网站用 Qoder Sites 发布**，可见范围设为「任何人都可访问」。**企业版账号无法设置公开访问，请用个人账号发布。**
- **提交材料**：项目网站网址、代码仓库、运行说明、验证样例、Qoder 开发记录（对话截图或录屏 + Qoder 账号 UID），以及鼓励提交的使用效果数据。

> ⚠️ 关于"主要开发在 Qoder 中完成"：目前仓库里的检索核心和网站是先搭好的骨架。为了符合要求，下面第 3～5 步的新功能请**全部在 Qoder 里完成**，并把每一步截图存进 `docs/qoder-records/`。这几个功能也直接对应评分里的"实用价值""使用效果"和"Qoder 开发实践"。

下面每一步都给了可以直接复制到 Qoder 对话框的提示词（引用块里的文字）。

---

## 第 0 步：准备

1. 下载 Qoder 并用**个人账号**登录。参赛选手可领取 2000 Qoder CN Credits（约 1 个月 Pro），领取方式见比赛页面。
2. 记下你的 **Qoder 账号 UID**（Qoder 设置 → 个人资料 → 编辑），提交时要用。
3. 确认电脑上有 Python 3.11 或更新版本：
   ```bash
   python3 --version
   ```
   低于 3.11 的话，先到 <https://www.python.org/downloads/> 安装新版。

## 第 1 步：拉取代码，用 Qoder 打开

```bash
git clone -b 2026-competition-ecosystem https://github.com/Luchutong/powercontext.git
```

打开 Qoder → **File → Open Folder** → 选择 `powercontext/qoder` 文件夹。

> 这个分支是队伍共用的，`gaode/` 等目录可能有队友的作品。只改 `qoder/` 目录里的东西。

## 第 2 步：让 Qoder 把项目跑起来 📸

在 Qoder 的 AI 对话框（Agent 模式）里发送：

> 请阅读 README.md 和 zhiku/ 目录，先用几句话总结这个项目的结构以及 seekdb 在哪里被使用。然后帮我：1）创建 Python 虚拟环境 .venv 并安装 requirements.txt；2）运行 `python -m zhiku verify` 确认验证样例全部 PASS；3）运行 `python -m pytest`；4）启动 `python -m zhiku serve --docs ./sample_docs`。每一步执行前先告诉我要运行什么命令。

浏览器打开 <http://127.0.0.1:8000>，搜索"发票什么时候要交给财务"，第一条是《报销与差旅规定》就对了。

📸 截图：Qoder 总结项目的对话、verify 全部 PASS 的终端输出。

**常见问题**
- 卡在下载模型：首次运行要下载约 95MB 的模型，默认走国内镜像 hf-mirror.com，稍等即可。
- `pip install` 失败：多半是 Python 版本低于 3.11，或 macOS 低于 15。把报错原文贴给 Qoder 让它处理（这本身也是"调试"环节的记录，📸 截图）。

## 第 3 步：在 Qoder 中开发「待办看板」📸

纪要里的待办自动汇总到一处，不用再翻纪要确认进度。

先让 Qoder 做需求拆解（📸 截图拆解结果）：

> 我想给知库增加「待办看板」：自动从所有已导入资料里汇总待办事项（Markdown 任务列表 `- [ ]` / `- [x]`），识别负责人和截止日期，在网页上按截止日期展示。请先帮我拆解需求、列出要改的文件和实现步骤，先不要写代码。

确认方案后：

> 按刚才的方案实现。要求：1）新建 zhiku/todos.py，待办数据要通过 seekdb 读取（collection.get 加 where_document 的 `$contains` 过滤），不要直接读文件；2）app.py 增加 GET /api/todos；3）web/index.html 右侧增加「待办」卡片，未完成的排在前面；4）在 tests/test_zhiku.py 补充测试并运行通过；5）在 zhiku/verify.py 里增加一个待办看板的验证样例，并重新运行 `python -m zhiku verify`。

不满意就直接用中文告诉 Qoder，比如"负责人没识别出来，帮我排查"（📸 调试过程）。

## 第 4 步：在 Qoder 中开发「使用记录」📸（为"使用效果"准备数据）

评分鼓励提交真实使用数据，而且数据要"真实可核对"。让知库自己记录使用情况：

> 给知库增加「使用记录」：每次搜索时，把时间、问题、结果数量、第一条结果的文件写入 seekdb 的另一个集合 zhiku_usage（metadata 里存时间和结果数，document 存问题文本）。新增 GET /api/usage，返回总搜索次数、按天的搜索次数、最常搜索的问题；网页底部加一个「使用统计」小面板。不要记录任何个人身份信息。补充测试并运行通过。

然后把知库给 3～5 位同学或同事用一周：用他们自己的资料，或者你们组的公共资料。记录：
- `/api/usage` 的统计截图；
- 使用前后的对比，例如"找一条报销规定：以前翻文件 5 分钟，现在 10 秒"；
- 简短的反馈（问卷或聊天截图，隐去姓名），以及根据反馈做的改进。改进也在 Qoder 里做，📸 截图。

## 第 5 步（可选）：在 Qoder 中开发「AI 总结回答」📸

需要阿里云百炼的 API Key（<https://bailian.console.aliyun.com>，有免费额度）。

> 给知库增加「AI 总结回答」：检索到前 5 个片段后，调用阿里云百炼通义千问（OpenAI 兼容接口，base_url 为 https://dashscope.aliyuncs.com/compatible-mode/v1，API Key 从环境变量 DASHSCOPE_API_KEY 读取，模型 qwen-plus），让模型只根据这些片段回答，并用 [1][2] 标注出处。没有设置 API Key 时自动跳过、只显示检索结果。新增 GET /api/ask，在网页搜索结果上方显示回答。README 的可选配置表里补上这个环境变量，只写占位值。

⚠️ API Key 只放在环境变量里，**不要写进代码，也不要提交到 GitHub**。

## 第 6 步：更新文档和项目网站 📸

先停掉服务（Ctrl+C），重新生成演示数据和检索记录：

```bash
python -m zhiku verify
python -m zhiku export-demo
```

再让 Qoder 更新网站和 README：

> site/index.html 是项目介绍网站。请：1）在「知库能做什么」里加上我新开发的功能，风格与现有卡片一致；2）启动服务，用浏览器重新截取界面截图，替换 site/assets/ 下的图片，并为新功能加一张截图；3）如果有使用统计数据，加一个「使用效果」小节展示真实数据；4）同步更新 README.md 的功能表、验证样例表和项目结构。完成后检查手机宽度下的排版。

网站要求包含：项目简介、解决的问题、核心功能、演示截图或视频、代码仓库链接、体验入口。现有页面都已具备，新功能补上即可。

## 第 7 步：用 Qoder Sites 发布网站 📸

`site/` 是纯静态网站，免费版 Qoder Sites 也能发布。

1. 在 Qoder 对话框发送：
   > 请把 site 目录作为静态网站，用 Qoder Sites 发布，站点名称"知库 ZhiKu"。
2. 检查预览：点一点「在线体验」的问题按钮，看看截图是否显示。
3. 点 Sites 面板右上角的 **发布（Publish）**，等待显示"已发布"。
4. **把可见范围改成「任何人都可访问」**（新网站默认只有自己可见，评委会打不开）：
   站点 **设置 → 发布与访问** → 选择所有人 → 确认"允许公开访问"。
5. 用浏览器**无痕窗口**打开网址，确认不登录也能访问。

之后网站内容有改动，都要**再点一次「发布」**线上才会更新。**评审结束前不要暂停或删除站点。**

## 第 8 步：整理提交材料

| 材料 | 怎么准备 |
| --- | --- |
| 项目网站网址 | 第 7 步的网址，填进 README 顶部表格 |
| 代码仓库 | <https://github.com/Luchutong/powercontext/tree/2026-competition-ecosystem/qoder> |
| 运行说明 | README「运行说明」，新增依赖时同步更新 requirements.txt |
| 验证样例 | README「验证样例」+ `docs/verify-log.md`（由 `python -m zhiku verify` 生成） |
| Qoder 开发记录 | 截图放进 `docs/qoder-records/`，填好里面的表格和 UID |
| 使用效果（鼓励） | 第 4 步收集的统计截图、对比数据、反馈，写进 README「使用效果」和网站 |

可以让 Qoder 帮你检查：

> 请对照 docs/QODER_GUIDE.md 开头的赛题要求和第 8 步的材料表，检查 qoder/ 目录还缺什么，并确认仓库里没有 API Key、密码等凭据。

## 第 9 步：提交代码

这个分支有队友在用，推送前先拉取最新代码：

```bash
git add qoder
git commit -m "feat(qoder): 完成待办看板、使用记录并发布项目网站"
git pull --rebase origin 2026-competition-ecosystem
git push origin 2026-competition-ecosystem
```

然后查看推送后的 Commit id：

```bash
git rev-parse HEAD
```

## 第 10 步：在测评平台提测

比赛页面 → **立即提测**：

| 字段 | 填写 |
| --- | --- |
| 代码仓库地址 | 自动填好，不用动 |
| Commit id | 第 9 步输出的 40 位字符串 |
| Branch | `2026-competition-ecosystem` |
| 作品链接 | Qoder Sites 网站网址 |

题面说提交时还要填 Qoder 账号 UID、上传其余材料。如果表单里有对应入口，一并填写和上传；以平台实际表单为准。

---

## 提交前自查清单

- [ ] 新功能都是在 Qoder 中开发的，`docs/qoder-records/` 里有截图和 UID
- [ ] `python -m zhiku verify` 全部 PASS，`docs/verify-log.md` 是最新的
- [ ] `python -m pytest` 通过
- [ ] 网站通过 Qoder Sites 用个人账号发布，可见范围是「任何人都可访问」，无痕窗口能打开
- [ ] README 顶部表格已填网址和 UID
- [ ] 代码已推送到 `2026-competition-ecosystem`，表单里的 Commit id 是推送后的那一个
- [ ] 仓库里没有 API Key、密码、个人隐私资料
