# 一页技术说明：拾知

**模型与量化。** 使用 MIT 许可的 BAAI BGE-small-zh-v1.5，ONNX 来源 Xenova/bge-small-zh-v1.5，固定 revision `75c43b069aac4d136ba6bc1122f995fedcfd2781`。采用上游发布的动态逐通道 INT8（QInt8/reduce_range）及 FP16 文件；模型和量化配置由 `npm run models` 获取并记录 SHA-256。CLS 池化、L2 归一化、512 维、256 tokens；查询加中文检索指令。

| 精度 | ONNX MB | 含同一套配置/分词器的模型总 MB | 总文件压缩比 |
| --- | ---: | ---: | ---: |
| FP32 | 94.85 | 95.29 | 1.00× |
| FP16 | 47.50 | 47.94 | 1.99× |
| INT8 | 24.01 | 24.45 | 3.90× |

**端侧执行。** Transformers.js 3.8.1 + 锁定的 ORT Web，Worker 内推理。优先显式 WebGPU/FP16，缺少适配器或 shader-f16、初始化/预热/推理失败时自动 WASM/INT8，界面显示后端和原因。全部资源由本机分发，禁用远程模型解析，无云端推理。真实 Chromium 验证了无 GPU 降级、FP16 初始化失败降级、缺少 shader-f16 自动降级，以及 FP32 WebGPU 正确性。当前仅有 SwiftShader；硬件 FP16 的性能和质量未测。最终策略禁用本运行时中产生错误语义向量的 INT8 WebGPU 组合。

**seekdb 与状态。** seekdb-js 1.4.0，独立知识库集合，保存原文、来源、文档摘要 ID、块号/位置、模型签名与 VECTOR(512)；cosine HNSW + 中文 ngram 全文索引。hybridSearch 调用 DBMS_HYBRID_SEARCH 在数据库生成并执行 RRF 融合 SQL（每路候选 20、k=5、常数 60）。稳定摘要 ID + upsert 保证重复导入幂等；增量导入末尾刷新索引，失败可重试。完整数据库操作经队列串行化保护 SDK 会话变量；切换知识库清空结果，localStorage 保存选择，数据持久化于 seekdb。

**优化与实测。** AMD Ryzen 7 8845H w/ Radeon 780M Graphics / 16 逻辑核，Chromium 149.0.7827.55，WASM 4 线程；seekdb 1.4.0、CPU 2、memory_budget=2G。相同 32 篇中文资料、20 条二值相关标注查询，各配置 3 次，新集合，关闭向量缓存。优化包括长度分组批处理、模型实例/文件缓存、有限 LRU、统一索引刷新。

| WASM 配置 | 文档块/秒 | 混合检索 ms | 查询总 ms | Recall@5 | nDCG@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| FP32 / 批 1 | 37.95 | 4.92 | 47.39 | 97.5% | 0.9236 |
| INT8 / 批 1 | 54.65 | 5.03 | 47.42 | 97.5% | 0.9398 |
| INT8 / 批 8 | 61.63 | 5.23 | 47.46 | 97.5% | 0.9254 |

中位吞吐组合提升 1.62×，同精度批处理提升 1.13×。8 次写入逐次刷新累计 4720.07 ms，统一刷新 602.72 ms。INT8 首次模型加载 1205.79 ms，后续文件缓存加载中位 130.67 ms；首次预热 144.73 ms。加载包含本机下载/读取和会话初始化；总查询含向量化、网络、SQL、DOM 和两帧绘制机会。小样本未观察到明显质量下降，不能外推通用精度或硬件加速幅度。

**复现。** [README](README.md) 提供依赖、模型获取、Docker/原生 seekdb 与启动步骤。`npm test`、`npm run test:e2e`、`npm run bench` 分别验证逻辑、完整流程和固定条件实验；[原始数据与详细结果](reports/RESULTS.md) 保留全部逐次计时、查询排名、文件哈希、数据库结构和验证边界。
