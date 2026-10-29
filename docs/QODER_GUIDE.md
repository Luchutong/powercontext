# 用 Qoder 完成开发和发布：一步步操作指南

项目骨架（检索核心、本地网页、项目网站）已经写好并测试通过。接下来的开发、发布和提交都在 **Qoder** 里完成。
下面每一步都给了可以直接复制到 Qoder 对话框里的提示词（引用块里的文字）。

> 小建议：每完成一步截一张图（Qoder 对话 + 运行结果），最后放进 README 或答辩材料，证明项目是用 Qoder 开发的。

---

## 第 0 步：准备环境

1. 到 <https://qoder.com> 下载 Qoder 并登录（注册时用你参赛的账号）。
2. 确认电脑上有 Python 3.11 或更新版本。在终端运行：
   ```bash
   python3 --version
   ```
   低于 3.11 的话，先到 <https://www.python.org/downloads/> 安装新版。
3. 确认装了 git：`git --version`。

## 第 1 步：把代码拉到本地，用 Qoder 打开

在终端运行（项目在仓库的 `zhiku` 分支上）：

```bash
git clone -b zhiku https://github.com/Luchutong/powercontext.git zhiku
```

然后打开 Qoder → **File → Open Folder** → 选择刚才的 `zhiku` 文件夹。

## 第 2 步：让 Qoder 把项目跑起来

在 Qoder 右侧的 AI 对话框（Agent 模式）里发送：

> 请阅读 README.md 和 zhiku/ 目录，了解这个项目。然后帮我：1）在项目根目录创建 Python 虚拟环境 .venv 并安装 requirements.txt；2）运行 `python -m zhiku ingest ./sample_docs` 导入示例资料；3）运行 `python -m pytest` 确认测试通过；4）启动 `python -m zhiku serve --docs ./sample_docs`。每一步执行前先告诉我要运行什么命令。

成功后，浏览器打开 <http://127.0.0.1:8000>，搜索"发票什么时候要交给财务"，能看到《报销与差旅规定》排在第一位就对了。

**常见问题**
- 卡在下载模型：首次运行要下载约 95MB 的中文向量模型，默认走国内镜像 hf-mirror.com，耐心等一下。
- `pip install pyseekdb` 失败：多半是 Python 版本低于 3.11，或者 macOS 版本低于 15。把报错原文贴给 Qoder，让它帮你处理。

## 第 3 步：用 Qoder 开发新功能「待办看板」

这是你用 Qoder 实际开发的第一个功能：自动从所有纪要里汇总待办事项，再也不用翻纪要确认进度。

> 给知库增加一个「待办看板」功能：
> 1. 在 zhiku/ 下新建 todos.py，从已导入的资料里提取待办事项：识别 Markdown 任务列表（`- [ ]` 未完成、`- [x]` 已完成），并尽量解析出负责人（冒号前面的名字）和截止日期（括号里的日期，如 10/22）。数据要从 seekdb 里读取（用 collection.get 并按 `$contains` 过滤 "- ["），不要直接读文件。
> 2. 在 zhiku/app.py 增加 GET /api/todos 接口。
> 3. 在 web/index.html 右侧边栏增加「待办」卡片，按截止日期排序，未完成的在前面。
> 4. 在 tests/test_zhiku.py 里补充测试，并运行 pytest 确认通过。

做完后刷新网页看看效果。如果哪里不满意，就直接用中文告诉 Qoder，比如"负责人没识别出来，帮我看看原因"。

## 第 4 步（可选，推荐）：用 Qoder 开发「AI 总结回答」

让知库不光找到原文，还能直接给出一句话答案。需要一个阿里云百炼的 API Key（<https://bailian.console.aliyun.com> 开通后可以领免费额度）。

> 给知库增加「AI 总结回答」：检索到前 5 个片段后，调用阿里云百炼的通义千问模型（OpenAI 兼容接口，base_url 为 https://dashscope.aliyuncs.com/compatible-mode/v1，API Key 从环境变量 DASHSCOPE_API_KEY 读取，模型 qwen-plus），让模型只根据这些片段回答问题，并在答案里用 [1][2] 标注出处。没有设置 API Key 时自动跳过，只显示检索结果。新增 GET /api/ask 接口，并在网页搜索结果上方显示 AI 回答。

⚠️ API Key 只放在环境变量里，**不要写进代码、不要提交到 GitHub**。

## 第 5 步：用你自己的真实场景测试

找一个你真实遇到过的场景（比如课题组纪要、课程笔记、实习周报），放几份**不涉及隐私**的资料进 `sample_docs/` 下的新文件夹，搜几个你平时真会问的问题。
效果不好时把问题和结果告诉 Qoder，让它帮你调整（比如切片大小 `CHUNK_SIZE`、关键词过滤规则 `_STOPWORDS`）。

> 评委最看重"真的解决了一个问题"，所以 README 里写一两句你自己的真实使用场景，会比功能列表更有说服力。

## 第 6 步：更新项目网站

先重新生成网站上的演示数据（需要先停掉第 2 步启动的服务，按 Ctrl+C）：

```bash
python -m zhiku export-demo
```

再让 Qoder 把新功能加到网站上：

> site/index.html 是这个项目的介绍网站。请在「知库能做什么」部分加上我刚开发的待办看板（以及 AI 总结回答，如果做了的话），风格和现有卡片保持一致；如果 zhiku/demo.py 需要导出待办数据，也一并修改，并重新运行 export-demo。完成后用浏览器预览 site/index.html，检查手机宽度下排版是否正常。

## 第 7 步：用 Qoder Sites 发布网站

`site/` 是纯静态网站（只有 index.html 和 demo-data.js），免费版 Qoder Sites 就能发布。

1. 在 Qoder 对话框里发送：
   > 请把 site 目录作为静态网站，用 Qoder Sites 发布。站点名称叫"知库 ZhiKu"。
2. Qoder 会生成预览。检查页面内容、点一点「在线体验」里的问题按钮。
3. 点 Sites 面板右上角的 **Publish（发布）**，等待显示"已发布"。
4. **改成公开访问**（新发布的网站默认只有你自己能看，评委会打不开！）：
   在网站的 **设置 → 发布与访问** 中把访问范围改成 **所有人**，按提示确认"允许公开访问"。
5. 复制网站地址，用浏览器的**无痕窗口**打开一次，确认不登录也能访问。

## 第 8 步：把网址写进项目，提交代码

> 把 README.md 第 5 行的"项目网站"替换成 <你的网站地址>，然后用 git 提交所有改动，提交信息写"完成待办看板并发布项目网站"，再推送到 origin 的 zhiku 分支。

如果是你自己在终端里操作：

```bash
git add -A
git commit -m "完成待办看板并发布项目网站"
git push origin zhiku
```

如果改了网站内容（比如 README 链接也展示在网站上），记得回到 Qoder Sites 再点一次 **Publish**，线上网站才会更新。

## 第 9 步：填写提测表单

先拿到最新提交的 Commit id：

```bash
git rev-parse HEAD
```

回到比赛页面 → **立即提测**：

| 字段 | 填写 |
| --- | --- |
| 代码仓库地址 | 自动填好，不用动 |
| Commit id | 上一条命令输出的 40 位字符串 |
| Branch | `zhiku` |
| 作品链接 | 第 7 步的 Qoder Sites 网站地址 |

点 **提交** 就完成了。

---

## 提交前自查清单

- [ ] 项目在 Qoder 里开发（第 3 步至少完成一个新功能），保留了截图
- [ ] 核心功能用到了 seekdb（`zhiku/store.py` 里的 `hybrid_search`）
- [ ] 网站通过 Qoder Sites 发布，并且**设成了公开访问**，无痕窗口能打开
- [ ] README 里填了网站地址，写了真实使用场景
- [ ] 代码已经 push，表单里的 Commit id 是 push 之后的那一个
- [ ] 仓库里没有 API Key、没有个人隐私资料
