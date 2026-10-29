import assert from 'node:assert/strict';
import { readFile, writeFile, mkdir } from 'node:fs/promises';
import { cpus, totalmem, platform, release } from 'node:os';
import { createHash } from 'node:crypto';
import { openBrowser, baseURL } from './browser.mjs';
import { MODEL, MODEL_FILES } from '../shared/model.mjs';
import { qualityMetrics } from '../shared/text.mjs';

const rawData = await readFile('public/data/evaluation.json', 'utf8');
const data = JSON.parse(rawData);
const trials = Number(process.env.BENCH_REPEATS || 3);
assert.ok(Number.isInteger(trials) && trials >= 1 && trials <= 10);
const modelManifest = JSON.parse(await readFile('public/models/bge-small-zh-v1.5/manifest.json', 'utf8'));
if (!modelManifest.files[MODEL_FILES.fp32]) throw new Error('Run npm run models to download the FP32 baseline before benchmarking.');
const sizes = Object.fromEntries(Object.entries(MODEL_FILES).map(([key, value]) => [key, modelManifest.files[value].bytes]));
const sharedFiles = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json'];
const sharedBytes = sharedFiles.reduce((sum, file) => sum + modelManifest.files[file].bytes, 0);
const runtimeTotalBytes = Object.fromEntries(Object.entries(sizes).map(([key, value]) => [key, value + sharedBytes]));
const median = values => { const sorted = [...values].sort((a, b) => a - b); const m = Math.floor(sorted.length / 2); return sorted.length % 2 ? sorted[m] : (sorted[m - 1] + sorted[m]) / 2; };
const mean = values => values.reduce((a, b) => a + b, 0) / values.length;
const report = {
  timestamp: new Date().toISOString(), model: MODEL, modelManifest,
  sizes, runtimeTotalBytes, sharedFiles, sharedBytes,
  compression: { q8: sizes.fp32 / sizes.q8, fp16: sizes.fp32 / sizes.fp16 },
  runtimeCompression: { q8: runtimeTotalBytes.fp32 / runtimeTotalBytes.q8, fp16: runtimeTotalBytes.fp32 / runtimeTotalBytes.fp16 },
  conditions: { hardware: cpus()[0].model, logicalCpus: cpus().length, memoryBytes: totalmem(),
    os: `${platform()} ${release()}`, datasetSha256: createHash('sha256').update(rawData).digest('hex'),
    documents: data.documents.length, queries: data.queries.length, k: 5, trials,
    database: await fetch(`${baseURL}/api/health`).then(r => r.json()),
    softwareGpu: process.env.SOFTWARE_GPU === '1',
    notes: '全部向量由提交应用的浏览器 Worker 实时生成。所有检索经 seekdb。固定人工标注小样本，不代表通用检索质量。查询计时包含两帧绘制机会；浏览器无远程请求。' },
  profiles: [],
};
const availableProfiles = [
  { name: 'fp32-wasm-b1', device: 'wasm', dtype: 'fp32', batchSize: 1 },
  { name: 'int8-wasm-b1', device: 'wasm', dtype: 'q8', batchSize: 1 },
  { name: 'int8-wasm-b8', device: 'wasm', dtype: 'q8', batchSize: 8 },
  { name: 'quantized-webgpu-b8', device: 'auto', dtype: 'auto', batchSize: 8, gpu: true },
  { name: 'fp32-webgpu-validation', device: 'webgpu', dtype: 'fp32', batchSize: 8, gpu: true },
];
const requestedProfiles = process.env.BENCH_PROFILES?.split(',').map(name => name.trim()).filter(Boolean);
if (requestedProfiles?.some(name => !availableProfiles.some(profile => profile.name === name))) {
  throw new Error(`BENCH_PROFILES must select from: ${availableProfiles.map(profile => profile.name).join(', ')}`);
}
const profiles = requestedProfiles ? availableProfiles.filter(profile => requestedProfiles.includes(profile.name)) : availableProfiles;
const output = process.env.BENCH_OUTPUT || 'reports/benchmark.json';
await mkdir('reports', { recursive: true });
for (const config of profiles) {
  console.log(`Profile ${config.name}`);
  const run = await openBrowser({ gpu: config.gpu });
  const profile = { config, environment: run.environment, trials: [] };
  report.profiles.push(profile);
  try {
    for (let trial = 0; trial < trials; trial++) {
      const { page } = run;
      const runtime = await page.evaluate(options => window.kb.initialize(options), { device: config.device, dtype: config.dtype });
      const collection = `bench_${Date.now().toString(36)}_${trial}`;
      await page.evaluate(name => window.kb.createCollection(name), collection);
      const imported = await page.evaluate(({ documents, batchSize }) => window.kb.ingest(
        documents.map(d => ({ source: d.id, text: d.text })), { batchSize, useCache: false }),
      { documents: data.documents, batchSize: config.batchSize });
      const record = { trial, runtime, imported, queries: [], cache: trial ? 'warm-browser-cache' : 'cold-browser-cache' };
      profile.trials.push(record);
      for (const mode of ['hybrid', 'vector']) {
        for (const query of data.queries) {
          const result = await page.evaluate(({ text, mode }) => window.kb.search(text, { mode, k: 5, useCache: false }), { text: query.text, mode });
          const ids = result.results.map(r => r.metadata.source);
          record.queries.push({ mode, text: query.text, relevant: query.relevant, results: ids,
            ...qualityMetrics(ids, query.relevant, 5), timings: result.timings });
        }
      }
      console.log(`  trial ${trial + 1}/${trials}: ${runtime.device}/${runtime.dtype}, embedding ${imported.timings.embeddingMs.toFixed(1)} ms, Recall@5 ${mean(record.queries.filter(q => q.mode === 'hybrid').map(q => q.recall)).toFixed(3)}`);
      await writeFile(output, JSON.stringify(report, null, 2) + '\n');
    }
    // Isolate refresh policy with identical model, precision, batch size and corpus.
    // Both writes use real browser-generated embeddings; no precomputed vectors are replayed.
    if (config.name === 'int8-wasm-b8') {
      profile.refreshExperiment = [];
      for (let repeat = 0; repeat < trials; repeat++) for (const each of [true, false]) {
        const collection = `refresh_${Date.now().toString(36)}_${each ? 'each' : 'once'}`;
        await run.page.evaluate(name => window.kb.createCollection(name), collection);
        const imported = await run.page.evaluate(({ docs, each }) => window.kb.ingest(
          docs.map(d => ({ source: d.id, text: d.text })), { batchSize: 8, writeBatchSize: 1, refreshEachBatch: each, useCache: false }),
        { docs: data.documents.slice(0, 8), each });
        profile.refreshExperiment.push({ repeat, each, imported });
      }
    }
    const all = profile.trials.flatMap(t => t.queries);
    profile.summary = {
      device: profile.trials[0].runtime.device, dtype: profile.trials[0].runtime.dtype,
      firstLoadMs: profile.trials[0].runtime.loadMs, firstWarmupMs: profile.trials[0].runtime.warmupMs,
      warmLoadMedianMs: profile.trials.length > 1 ? median(profile.trials.slice(1).map(t => t.runtime.loadMs)) : null,
      embeddingMedianMs: median(profile.trials.map(t => t.imported.timings.embeddingMs)),
      chunksPerSecond: median(profile.trials.map(t => t.imported.chunks * 1000 / t.imported.timings.embeddingMs)),
      writeMedianMs: median(profile.trials.map(t => t.imported.timings.writeMs)),
      refreshMedianMs: median(profile.trials.map(t => t.imported.timings.refreshMs)),
      modes: Object.fromEntries(['hybrid', 'vector'].map(mode => {
        const queries = all.filter(q => q.mode === mode);
        return [mode, { recall5: mean(queries.map(q => q.recall)), ndcg5: mean(queries.map(q => q.ndcg)),
          firstQueryTimings: profile.trials[0].queries.find(q => q.mode === mode).timings,
          queryEmbeddingMedianMs: median(queries.map(q => q.timings.embeddingMs)),
          searchMedianMs: median(queries.map(q => q.timings.searchMs)),
          endToEndMedianMs: median(queries.map(q => q.timings.totalMs)) }];
      })),
    };
    assert.deepEqual(run.errors, []);
    assert.deepEqual(run.remoteRequests, []);
    assert.ok(profile.summary.modes.hybrid.recall5 >= 0.8, 'Fixed-dataset recall must remain useful');
    await writeFile(output, JSON.stringify(report, null, 2) + '\n');
  } finally { await run.browser.close(); }
}
console.table(report.profiles.map(p => ({ profile: p.config.name, ...p.summary,
  modes: undefined, recall5: p.summary.modes.hybrid.recall5, ndcg5: p.summary.modes.hybrid.ndcg5 })));
console.log(`Raw measurements: ${output}`);
