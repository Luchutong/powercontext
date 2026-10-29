# 固定条件实验记录

原始数据：[recheck-benchmark.json](recheck-benchmark.json)；文件哈希：[model-manifest.json](model-manifest.json)。部署、原始表结构和复查的连接恢复记录见 [RECHECK.md](RECHECK.md)、[seekdb.json](seekdb.json) 与 [recheck-seekdb.json](recheck-seekdb.json)。本表由 `BENCH_INPUT=reports/recheck-benchmark.json node scripts/summarize.mjs` 从原始记录生成。首版完整五配置实验仍保留在 [benchmark.json](benchmark.json)。

## 条件与方法

- 运行时间：2026-10-29T11:40:53.001Z（UTC）；设备：AMD Ryzen 7 8845H w/ Radeon 780M Graphics，16 逻辑核，系统内存 9.71 GiB，linux 6.6.87.2-microsoft-standard-WSL2。
- 浏览器：Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/149.0.7827.55 Safari/537.36。WASM 4 线程；跨源隔离 true。软件 GPU：false；SwiftShader 结果仅用于后端/正确性验证。
- seekdb：5.7.25-OceanBase seekdb-v1.4.0.0，CPU 2，memory_budget=2G，datafile_size=1G，datafile_maxsize=4G，log_disk_size=2G；完整 SQL 查询记录见 seekdb.json。
- 固定语料 32 篇中文短文、20 个问题，数据 SHA-256：`2126b28a3afeef14ec83efa150f9fd59ae533c695ce4a540740e8938f5b3617c`。同一评测集、每配置 3 轮、每轮新建集合，关闭向量结果缓存。每轮分别执行混合和向量检索。Recall@5/nDCG@5 对来源文档去重后做查询宏平均；标注采用二值相关性。
- 吞吐为文档块数 / Worker 推理时间；数据库搜索计时包含 SDK 的 SET、GET_SQL 与实际 SELECT；查询总时间包括推理、网络、数据库、DOM 更新和两帧绘制机会。计时不是物理显示器呈现时间。

## 文件体积

| 精度 | ONNX 字节数 | 运行所需模型文件总字节数 | 总文件压缩比 |
| --- | ---: | ---: | ---: |
| fp32 | 94851877 | 95292210 | 1.00× |
| fp16 | 47497427 | 47937760 | 1.99× |
| q8 | 24010842 | 24451175 | 3.90× |

统一口径：每种精度只计它实际使用的一个 ONNX 文件，加同一套 config.json、tokenizer.json、tokenizer_config.json、special_tokens_map.json，共 440333 字节。量化配置、溯源用词表、WASM 运行时和其他精度文件不计入模型大小。下载目录同时保存三种精度用于对照，不能将该目录总大小当成一种模型的大小。

## 检索与性能

| 配置 / 实际后端 | 文本块/秒 | seekdb 混合检索中位 ms | 查询总时间中位 ms | Recall@5 | nDCG@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| fp32-wasm-b1 (wasm/fp32) | 38.45 | 5.43 | 47.59 | 97.5% | 0.9236 |
| int8-wasm-b1 (wasm/q8) | 45.97 | 6.12 | 47.45 | 97.5% | 0.9398 |
| int8-wasm-b8 (wasm/q8) | 45.49 | 6.38 | 47.58 | 97.5% | 0.9254 |

INT8/批 1 对 FP32/批 1 的吞吐倍数为 1.20×；相同 INT8 精度下，批 8 对批 1 为 0.99×；组合配置对基线为 1.18×。本次未观察到批 8 的吞吐收益，不能沿用旧环境中的批处理提升幅度。混合检索 nDCG@5 变化 0.0018，纯向量 nDCG@5 从 0.9276 到 0.9276。小样本结果不外推为通用精度提升；不同批次 padding 和浮点舍入可影响边界候选顺序。

刷新策略实验使用相同的 8 篇文档、INT8/WASM、推理批大小 8、写入批大小 1，每种策略 3 次。逐次刷新累计中位 4925.21 ms，末尾一次刷新 503.28 ms，减少 89.8%。原始记录保留 embedding/write/refresh/total 全部阶段。

## 首次与缓存加载

| 配置 | 首次加载 ms | 首次预热 ms | 后续缓存加载中位 ms |
| --- | ---: | ---: | ---: |
| fp32-wasm-b1 | 2528.34 | 109.07 | 301.44 |
| int8-wasm-b1 | 1310.64 | 135.21 | 151.60 |
| int8-wasm-b8 | 1480.89 | 133.40 | 177.88 |

首次使用独立浏览器上下文的空 Cache Storage，后续模型重载保留浏览器模型文件缓存；每次模型重载都释放并重新建立推理会话。加载时间包含资源下载/读取与会话初始化，预热单独计时。Worker 的 modelResources 记录 ONNX 的本机 HTTP 下载耗时和传输字节；命中 Cache Storage 后通常没有新的 HTTP Resource Timing 条目。这里测量的是本机模型分发，不是互联网带宽。

## 后端边界

当前软件适配器不支持 shader-f16，自动配置正确降级至 WASM/INT8；因此自动配置行并非硬件 FP16 GPU 的性能。FP32 WebGPU 对照单独启用，供验证浏览器 WebGPU 执行和语义正确性。缺少可用硬件 GPU，未测量 FP16 硬件吞吐或 FP16 检索质量。此前发现动态 INT8 在此 ORT/SwiftShader 组合产生不准确向量，最终默认策略已禁用该组合，没有将其结果混入有效质量数据。

首版实验使用原生 seekdb，当时 Docker 拉取返回 EOF。后续复查已在空模型目录和新 Docker 数据卷上验证默认一键启动，并修复数据库重启后的连接恢复。最新验证范围、部署和实际日志见 [RECHECK.md](RECHECK.md)。
