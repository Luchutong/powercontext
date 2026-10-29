import { readFile, writeFile } from 'node:fs/promises';
const report = JSON.parse(await readFile('reports/benchmark.json', 'utf8'));
const median = values => { const sorted = values.sort((a, b) => a - b); const middle = Math.floor(sorted.length / 2); return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2; };
const f = (n, digits = 2) => n === null || n === undefined ? '—' : n.toFixed(digits);
const profiles = report.profiles.filter(p => p.summary);
const baseline = profiles.find(p => p.config.name === 'fp32-wasm-b1').summary;
const quant = profiles.find(p => p.config.name === 'int8-wasm-b1').summary;
const optimized = profiles.find(p => p.config.name === 'int8-wasm-b8').summary;
const refresh = profiles.find(p => p.refreshExperiment)?.refreshExperiment;
const eachMs = median(refresh.filter(r => r.each).map(r => r.imported.timings.refreshMs));
const onceMs = median(refresh.filter(r => !r.each).map(r => r.imported.timings.refreshMs));
const rows = profiles.map(p => {
  const s = p.summary, q = s.modes.hybrid;
  return `| ${p.config.name} (${s.device}/${s.dtype}) | ${f(s.chunksPerSecond)} | ${f(q.searchMedianMs)} | ${f(q.endToEndMedianMs)} | ${f(q.recall5 * 100, 1)}% | ${f(q.ndcg5, 4)} |`;
}).join('\n');
const sizeRows = ['fp32', 'fp16', 'q8'].map(dtype => `| ${dtype} | ${report.sizes[dtype]} | ${report.runtimeTotalBytes[dtype]} | ${dtype === 'fp32' ? '1.00' : f(report.runtimeCompression[dtype])}× |`).join('\n');
const loadRows = profiles.map(p => `| ${p.config.name} | ${f(p.summary.firstLoadMs)} | ${f(p.summary.firstWarmupMs)} | ${f(p.summary.warmLoadMedianMs)} |`).join('\n');
const detail = `# 固定条件实验记录

原始数据：[benchmark.json](benchmark.json)；数据库参数及实际表结构：[seekdb.json](seekdb.json)；文件哈希：[model-manifest.json](model-manifest.json)；端到端验证：[e2e.json](e2e.json)。本表由 \`node scripts/summarize.mjs\` 从原始记录生成。

## 条件与方法

- 运行时间：${report.timestamp}（UTC）；设备：${report.conditions.hardware}，${report.conditions.logicalCpus} 逻辑核，系统内存 ${f(report.conditions.memoryBytes / 1024 ** 3)} GiB，${report.conditions.os}。
- 浏览器：${profiles[0].environment.browser}。WASM ${profiles[0].trials[0].runtime.threads} 线程；跨源隔离 ${profiles[0].environment.crossOriginIsolated}。软件 GPU：${report.conditions.softwareGpu}；SwiftShader 结果仅用于后端/正确性验证。
- seekdb：${report.conditions.database.version}，CPU 2，memory_budget=2G，datafile_size=1G，datafile_maxsize=4G，log_disk_size=2G；完整 SQL 查询记录见 seekdb.json。
- 固定语料 ${report.conditions.documents} 篇中文短文、${report.conditions.queries} 个问题，数据 SHA-256：\`${report.conditions.datasetSha256}\`。同一评测集、每配置 ${report.conditions.trials} 轮、每轮新建集合，关闭向量结果缓存。每轮分别执行混合和向量检索。Recall@5/nDCG@5 对来源文档去重后做查询宏平均；标注采用二值相关性。
- 吞吐为文档块数 / Worker 推理时间；数据库搜索计时包含 SDK 的 SET、GET_SQL 与实际 SELECT；查询总时间包括推理、网络、数据库、DOM 更新和两帧绘制机会。计时不是物理显示器呈现时间。

## 文件体积

| 精度 | ONNX 字节数 | 运行所需模型文件总字节数 | 总文件压缩比 |
| --- | ---: | ---: | ---: |
${sizeRows}

统一口径：每种精度只计它实际使用的一个 ONNX 文件，加同一套 config.json、tokenizer.json、tokenizer_config.json、special_tokens_map.json，共 ${report.sharedBytes} 字节。量化配置、溯源用词表、WASM 运行时和其他精度文件不计入模型大小。下载目录同时保存三种精度用于对照，不能将该目录总大小当成一种模型的大小。

## 检索与性能

| 配置 / 实际后端 | 文本块/秒 | seekdb 混合检索中位 ms | 查询总时间中位 ms | Recall@5 | nDCG@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
${rows}

INT8/批 1 对 FP32/批 1 的吞吐倍数为 ${f(quant.chunksPerSecond / baseline.chunksPerSecond)}×；相同 INT8 精度下，批 8 对批 1 为 ${f(optimized.chunksPerSecond / quant.chunksPerSecond)}×；组合配置对基线为 ${f(optimized.chunksPerSecond / baseline.chunksPerSecond)}×。混合检索 nDCG@5 变化 ${f(optimized.modes.hybrid.ndcg5 - baseline.modes.hybrid.ndcg5, 4)}，纯向量 nDCG@5 从 ${f(baseline.modes.vector.ndcg5, 4)} 到 ${f(optimized.modes.vector.ndcg5, 4)}。小样本结果不外推为通用精度提升；不同批次 padding 和浮点舍入可影响边界候选顺序。

刷新策略实验使用相同的 8 篇文档、INT8/WASM、推理批大小 8、写入批大小 1，每种策略 ${report.conditions.trials} 次。逐次刷新累计中位 ${f(eachMs)} ms，末尾一次刷新 ${f(onceMs)} ms，减少 ${f((1 - onceMs / eachMs) * 100, 1)}%。原始记录保留 embedding/write/refresh/total 全部阶段。

## 首次与缓存加载

| 配置 | 首次加载 ms | 首次预热 ms | 后续缓存加载中位 ms |
| --- | ---: | ---: | ---: |
${loadRows}

首次使用独立浏览器上下文的空 Cache Storage，后续模型重载保留浏览器模型文件缓存；每次模型重载都释放并重新建立推理会话。加载时间包含资源下载/读取与会话初始化，预热单独计时。Worker 的 modelResources 记录 ONNX 的本机 HTTP 下载耗时和传输字节；命中 Cache Storage 后通常没有新的 HTTP Resource Timing 条目。这里测量的是本机模型分发，不是互联网带宽。

## 后端边界

当前软件适配器不支持 shader-f16，自动配置正确降级至 WASM/INT8；因此自动配置行并非硬件 FP16 GPU 的性能。FP32 WebGPU 对照单独启用，供验证浏览器 WebGPU 执行和语义正确性。缺少可用硬件 GPU，未测量 FP16 硬件吞吐或 FP16 检索质量。此前发现动态 INT8 在此 ORT/SwiftShader 组合产生不准确向量，最终默认策略已禁用该组合，没有将其结果混入有效质量数据。

Docker 镜像拉取受当前网络 EOF 影响。实际数据库验证通过提交的原生启动脚本完成，使用官方 pylibseekdb 1.4.0.post1 所带 server；Compose 已通过配置校验。技术说明不声称容器已经启动验证。
`;
await writeFile('reports/RESULTS.md', detail);
const technical = `# 一页技术说明：拾知

**模型与量化。** 使用 MIT 许可的 BAAI BGE-small-zh-v1.5，ONNX 来源 Xenova/bge-small-zh-v1.5，固定 revision \`75c43b069aac4d136ba6bc1122f995fedcfd2781\`。采用上游发布的动态逐通道 INT8（QInt8/reduce_range）及 FP16 文件；模型和量化配置由 \`npm run models\` 获取并记录 SHA-256。CLS 池化、L2 归一化、512 维、256 tokens；查询加中文检索指令。

| 精度 | ONNX MB | 含同一套配置/分词器的模型总 MB | 总文件压缩比 |
| --- | ---: | ---: | ---: |
| FP32 | ${f(report.sizes.fp32 / 1e6)} | ${f(report.runtimeTotalBytes.fp32 / 1e6)} | 1.00× |
| FP16 | ${f(report.sizes.fp16 / 1e6)} | ${f(report.runtimeTotalBytes.fp16 / 1e6)} | ${f(report.runtimeCompression.fp16)}× |
| INT8 | ${f(report.sizes.q8 / 1e6)} | ${f(report.runtimeTotalBytes.q8 / 1e6)} | ${f(report.runtimeCompression.q8)}× |

**端侧执行。** Transformers.js 3.8.1 + 锁定的 ORT Web，Worker 内推理。优先显式 WebGPU/FP16，缺少适配器或 shader-f16、初始化/预热/推理失败时自动 WASM/INT8，界面显示后端和原因。全部资源由本机分发，禁用远程模型解析，无云端推理。真实 Chromium 验证了无 GPU 降级、FP16 初始化失败降级、缺少 shader-f16 自动降级，以及 FP32 WebGPU 正确性。当前仅有 SwiftShader；硬件 FP16 的性能和质量未测。最终策略禁用本运行时中产生错误语义向量的 INT8 WebGPU 组合。

**seekdb 与状态。** seekdb-js 1.4.0，独立知识库集合，保存原文、来源、文档摘要 ID、块号/位置、模型签名与 VECTOR(512)；cosine HNSW + 中文 ngram 全文索引。hybridSearch 调用 DBMS_HYBRID_SEARCH 在数据库生成并执行 RRF 融合 SQL（每路候选 20、k=5、常数 60）。稳定摘要 ID + upsert 保证重复导入幂等；增量导入末尾刷新索引，失败可重试。完整数据库操作经队列串行化保护 SDK 会话变量；切换知识库清空结果，localStorage 保存选择，数据持久化于 seekdb。

**优化与实测。** ${report.conditions.hardware} / ${report.conditions.logicalCpus} 逻辑核，Chromium ${profiles[0].environment.browser.match(/HeadlessChrome\/([\d.]+)/)?.[1] || ''}，WASM 4 线程；seekdb 1.4.0、CPU 2、memory_budget=2G。相同 32 篇中文资料、20 条二值相关标注查询，各配置 3 次，新集合，关闭向量缓存。优化包括长度分组批处理、模型实例/文件缓存、有限 LRU、统一索引刷新。

| WASM 配置 | 文档块/秒 | 混合检索 ms | 查询总 ms | Recall@5 | nDCG@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| FP32 / 批 1 | ${f(baseline.chunksPerSecond)} | ${f(baseline.modes.hybrid.searchMedianMs)} | ${f(baseline.modes.hybrid.endToEndMedianMs)} | ${f(baseline.modes.hybrid.recall5 * 100, 1)}% | ${f(baseline.modes.hybrid.ndcg5, 4)} |
| INT8 / 批 1 | ${f(quant.chunksPerSecond)} | ${f(quant.modes.hybrid.searchMedianMs)} | ${f(quant.modes.hybrid.endToEndMedianMs)} | ${f(quant.modes.hybrid.recall5 * 100, 1)}% | ${f(quant.modes.hybrid.ndcg5, 4)} |
| INT8 / 批 8 | ${f(optimized.chunksPerSecond)} | ${f(optimized.modes.hybrid.searchMedianMs)} | ${f(optimized.modes.hybrid.endToEndMedianMs)} | ${f(optimized.modes.hybrid.recall5 * 100, 1)}% | ${f(optimized.modes.hybrid.ndcg5, 4)} |

中位吞吐组合提升 ${f(optimized.chunksPerSecond / baseline.chunksPerSecond)}×，同精度批处理提升 ${f(optimized.chunksPerSecond / quant.chunksPerSecond)}×。8 次写入逐次刷新累计 ${f(eachMs)} ms，统一刷新 ${f(onceMs)} ms。INT8 首次模型加载 ${f(optimized.firstLoadMs)} ms，后续文件缓存加载中位 ${f(optimized.warmLoadMedianMs)} ms；首次预热 ${f(optimized.firstWarmupMs)} ms。加载包含本机下载/读取和会话初始化；总查询含向量化、网络、SQL、DOM 和两帧绘制机会。小样本未观察到明显质量下降，不能外推通用精度或硬件加速幅度。

**复现。** [README](README.md) 提供依赖、模型获取、Docker/原生 seekdb 与启动步骤。\`npm test\`、\`npm run test:e2e\`、\`npm run bench\` 分别验证逻辑、完整流程和固定条件实验；[原始数据与详细结果](reports/RESULTS.md) 保留全部逐次计时、查询排名、文件哈希、数据库结构和验证边界。
`;
await writeFile('TECHNICAL.md', technical);
console.log('Generated reports/RESULTS.md and TECHNICAL.md from raw measurements');
