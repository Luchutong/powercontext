import { env, pipeline } from '@huggingface/transformers';
import { MODEL } from '../shared/model.mjs';
import { backendCandidates } from './backend.mjs';

env.allowRemoteModels = false;
env.allowLocalModels = true;
env.useBrowserCache = true;
env.backends.onnx.wasm.wasmPaths = '/ort/';
env.backends.onnx.wasm.numThreads = globalThis.crossOriginIsolated
  ? Math.max(1, Math.min(4, Math.floor((navigator.hardwareConcurrency || 2) / 2))) : 1;
let extractor;
let runtime;
let loadingOptions = {};
const cache = new Map();
const CACHE_LIMIT = 256;

async function initialize(options, notify) {
  loadingOptions = options;
  await extractor?.dispose();
  extractor = undefined;
  cache.clear();
  const candidates = await backendCandidates(options);
  let reason = candidates[0].reason || '';
  for (const candidate of candidates) {
    const start = performance.now();
    try {
      performance.clearResourceTimings();
      notify({ phase: 'loading', message: `加载 ${candidate.device.toUpperCase()} / ${candidate.dtype}`, ...candidate });
      extractor = await pipeline('feature-extraction', MODEL.path, {
        device: candidate.device, dtype: candidate.dtype,
        progress_callback: progress => notify({ phase: 'download', ...progress }),
      });
      const loadMs = performance.now() - start;
      const resources = performance.getEntriesByType('resource')
        .filter(entry => entry.name.includes(MODEL.path) && entry.name.includes('.onnx'))
        .map(entry => ({ name: new URL(entry.name).pathname, durationMs: entry.duration,
          transferBytes: entry.transferSize, bodyBytes: entry.decodedBodySize }));
      const warmStart = performance.now();
      // Compile kernels before announcing readiness. Also catches first-run GPU failures.
      await extractor(['模型预热'], { pooling: 'cls', normalize: true, truncation: true, max_length: MODEL.maxLength });
      runtime = { device: candidate.device, dtype: candidate.dtype, reason,
        loadMs, modelResources: resources, warmupMs: performance.now() - warmStart,
        threads: env.backends.onnx.wasm.numThreads, signature: MODEL.signature };
      notify({ phase: 'ready', runtime });
      return runtime;
    } catch (error) {
      try { await extractor?.dispose(); } catch {}
      extractor = undefined;
      if (candidate.device === 'wasm') throw new Error(`本地模型加载失败，请运行 npm run models。${error.message}`);
      reason = `WebGPU 初始化或预热失败，已自动降级至 WASM：${error.message}`;
      notify({ phase: 'fallback', message: reason });
    }
  }
}

async function embed({ texts, kind = 'document', batchSize = 8, useCache = true }, notify) {
  if (!extractor) await initialize(loadingOptions, notify);
  if (!Array.isArray(texts) || texts.length > 20000 || texts.some(t => typeof t !== 'string') ||
      !['document', 'query'].includes(kind) || !Number.isInteger(batchSize) || batchSize < 1 || batchSize > 32) {
    throw new Error('Invalid embedding request');
  }
  const start = performance.now();
  const output = new Array(texts.length);
  const pending = new Map();
  let cacheHits = 0;
  for (let i = 0; i < texts.length; i++) {
    const input = (kind === 'query' ? MODEL.queryPrefix : '') + texts[i];
    if (useCache && cache.has(input)) {
      const value = cache.get(input); cache.delete(input); cache.set(input, value);
      output[i] = value; cacheHits++;
    } else if (pending.has(input)) pending.get(input).indices.push(i);
    else pending.set(input, { input, indices: [i] });
  }
  // Similar lengths in one batch reduce padding; indices restore caller order.
  const work = [...pending.values()].sort((a, b) => a.input.length - b.input.length);
  let completed = cacheHits;
  for (let i = 0; i < work.length; i += batchSize) {
    const batch = work.slice(i, i + batchSize);
    try {
      const tensor = await extractor(batch.map(x => x.input), {
        pooling: 'cls', normalize: true, truncation: true, max_length: MODEL.maxLength,
      });
      const rows = tensor.tolist();
      rows.forEach((row, j) => {
        for (const index of batch[j].indices) output[index] = row;
        completed += batch[j].indices.length;
        if (useCache) {
          cache.set(batch[j].input, row);
          while (cache.size > CACHE_LIMIT) cache.delete(cache.keys().next().value);
        }
      });
      notify({ phase: 'embedding', completed, total: texts.length });
    } catch (error) {
      if (runtime.device !== 'webgpu') throw error;
      await initialize({ device: 'wasm', dtype: loadingOptions.dtype === 'fp32' ? 'fp32' : 'q8' }, notify);
      runtime.reason = `WebGPU 推理失败，已自动降级至 WASM：${error.message}`;
      notify({ phase: 'ready', runtime });
      // Restart the request so it does not mix rows produced before/after a failure.
      return embed({ texts, kind, batchSize, useCache }, notify);
    }
  }
  return { embeddings: output, runtime, cacheHits, timings: { embeddingMs: performance.now() - start } };
}

let queue = Promise.resolve();
self.onmessage = ({ data: { id, type, options } }) => {
  queue = queue.then(async () => {
    const notify = progress => self.postMessage({ id, progress });
    try {
      const result = type === 'init' ? await initialize(options || {}, notify)
        : type === 'embed' ? await embed(options, notify) : (() => { throw new Error('Unknown worker operation'); })();
      self.postMessage({ id, result });
    } catch (error) { self.postMessage({ id, error: error.message }); }
  });
};
