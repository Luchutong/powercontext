import './style.css';
import { MODEL } from '../shared/model.mjs';
import { splitDocument } from '../shared/text.mjs';
import { Embedder } from './embedding.mjs';

const $ = id => document.getElementById(id);
const ms = value => `${value.toFixed(1)} ms`;
let selected = localStorage.getItem('stepfun.collection') || 'personal';
let busy = false;
let runtime;
const metrics = [];
function status(message, error = false) { $('status').textContent = message; $('status').classList.toggle('error', error); }
function showRuntime(value) {
  runtime = value;
  $('backend').textContent = `${value.device.toUpperCase()} · ${value.dtype === 'q8' ? 'INT8' : value.dtype.toUpperCase()}`;
  $('backend-reason').textContent = value.reason || '本地推理已就绪';
  $('time-model').textContent = `${ms(value.loadMs)} / ${ms(value.warmupMs)}`;
}
const embedder = new Embedder(progress => {
  if (progress.phase === 'embedding') {
    $('progress').value = progress.total ? progress.completed / progress.total * 100 : 100;
    status(`本地向量化 ${progress.completed} / ${progress.total}`);
  } else if (progress.phase === 'ready') showRuntime(progress.runtime);
  else if (progress.message) status(progress.message);
  else if (progress.status === 'progress') status(`加载模型 ${progress.file}：${(progress.progress || 0).toFixed(0)}%`);
});
async function api(path, body) {
  const response = await fetch(`/api${path}`, body === undefined ? {} : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || `HTTP ${response.status}`);
  return value;
}
async function exclusive(operation) {
  if (busy) throw new Error('请等待当前操作完成');
  busy = true;
  const controls = [...document.querySelectorAll('button, input, textarea, select')];
  controls.forEach(control => { control.disabled = true; });
  try { return await operation(); }
  catch (error) { status(error.message, true); throw error; }
  finally { busy = false; controls.forEach(control => { control.disabled = false; }); }
}
async function initializeInternal(options = {}) {
  runtime = undefined;
  const value = await embedder.initialize(options);
  showRuntime(value);
  metrics.push({ type: 'model', at: new Date().toISOString(), runtime: value });
  return value;
}
async function ensureModel() { if (!runtime) await initializeInternal(); }
async function updateCollections() {
  const collections = await api('/collections');
  if (!collections.some(c => c.name === selected)) selected = 'personal';
  $('collection').replaceChildren(...collections.map(item => {
    const option = new Option(`${item.name} · ${item.count}`, item.name);
    option.selected = item.name === selected;
    return option;
  }));
  $('count').textContent = collections.find(c => c.name === selected)?.count || 0;
  localStorage.setItem('stepfun.collection', selected);
  return collections;
}
const digest = async text => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text))))
  .map(x => x.toString(16).padStart(2, '0')).join('');

async function ingestInternal(documents, { batchSize = 8, writeBatchSize = 128, refreshEachBatch = false, useCache = true } = {}) {
  if (!Array.isArray(documents) || !documents.length || documents.length > 100 ||
      !Number.isInteger(writeBatchSize) || writeBatchSize < 1 || writeBatchSize > 128) throw new Error('请选择 1–100 份文档');
  const started = performance.now();
  const collection = selected;
  const chunks = [];
  for (const doc of documents) {
    if (typeof doc.text !== 'string' || typeof doc.source !== 'string' || !doc.source.trim() || doc.source.length > 512 ||
        new TextEncoder().encode(doc.text).length > 2 * 1024 * 1024) throw new Error('文档须为 UTF-8 文本，名称最多 512 字符，正文不超过 2 MB');
    const documentId = await digest(`${doc.source}\u0000${doc.text}`);
    for (const chunk of splitDocument(doc.text)) {
      chunks.push({ ...chunk, source: doc.source, documentId, id: await digest(`${documentId}:${chunk.start}:${chunk.text}`) });
    }
    if (chunks.length > 10000) throw new Error('单次导入最多 10000 个文本块，请分批导入');
  }
  if (!chunks.length) throw new Error('文档内容为空');
  await ensureModel();
  let embeddingMs = 0, writeMs = 0, refreshMs = 0, cacheHits = 0, result;
  // Bound memory and payload sizes; refresh once at the import boundary by default.
  for (let offset = 0; offset < chunks.length; offset += writeBatchSize) {
    const part = chunks.slice(offset, offset + writeBatchSize);
    const encoded = await embedder.embed(part.map(c => c.text), { batchSize, useCache });
    showRuntime(encoded.runtime);
    embeddingMs += encoded.timings.embeddingMs;
    cacheHits += encoded.cacheHits;
    status(`写入 ${collection}：${Math.min(offset + part.length, chunks.length)} / ${chunks.length}`);
    result = await api(`/collections/${collection}/documents`, { signature: MODEL.signature,
      records: part.map((c, i) => ({ ...c, embedding: encoded.embeddings[i] })), refresh: refreshEachBatch });
    writeMs += result.timings.writeMs;
    refreshMs += result.timings.refreshMs;
    $('progress').value = Math.min(offset + part.length, chunks.length) / chunks.length * 100;
  }
  if (!refreshEachBatch) {
    status('正在刷新索引…');
    result = await api(`/collections/${collection}/refresh`, {});
    refreshMs += result.timings.refreshMs;
  }
  const record = { type: 'import', collection, documents: documents.length, chunks: chunks.length,
    count: result.count, runtime, batchSize, writeBatchSize, refreshEachBatch, cacheHits,
    timings: { embeddingMs, writeMs, refreshMs, totalMs: performance.now() - started },
    at: new Date().toISOString() };
  metrics.push(record);
  await updateCollections();
  $('time-embed').textContent = ms(embeddingMs);
  $('time-write').textContent = `${ms(writeMs)} / ${ms(refreshMs)}`;
  status(`已导入 ${chunks.length} 个文本块，知识库共 ${result.count} 块；索引已刷新。`);
  return record;
}
function renderResults(result) {
  $('results').replaceChildren();
  if (!result.results.length) {
    const p = document.createElement('p'); p.className = 'empty'; p.textContent = '当前知识库暂无匹配结果，请先导入资料。';
    $('results').append(p); return;
  }
  result.results.forEach((item, index) => {
    const article = document.createElement('article'); article.className = 'result';
    const head = document.createElement('div'); head.className = 'result-head';
    const rank = document.createElement('span'); rank.className = 'rank'; rank.textContent = String(index + 1).padStart(2, '0');
    const title = document.createElement('h3'); title.textContent = item.metadata?.source || item.id;
    head.append(rank, title);
    const text = document.createElement('p'); text.textContent = item.text;
    const meta = document.createElement('small'); meta.textContent = `文本块 ${(item.metadata?.chunk ?? 0) + 1} · 字符 ${item.metadata?.start ?? 0}–${item.metadata?.end ?? 0} · ${result.mode === 'hybrid' ? 'RRF 分数' : '余弦距离'} ${item.score?.toFixed(5) ?? '—'}`;
    article.append(head, text, meta); $('results').append(article);
  });
}
async function searchInternal(text, { k = 5, mode = 'hybrid', useCache = true } = {}) {
  if (typeof text !== 'string' || !text.trim() || text.length > 500) throw new Error('请输入 1–500 字符的查询');
  const started = performance.now();
  const collection = selected;
  await ensureModel();
  const encoded = await embedder.embed([text], { kind: 'query', batchSize: 1, useCache });
  showRuntime(encoded.runtime);
  const result = await api(`/collections/${collection}/search`, { signature: MODEL.signature,
    text, embedding: encoded.embeddings[0], k, mode });
  renderResults(result);
  // Include a paint opportunity, rather than stopping the clock at the HTTP response.
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  result.timings = { ...result.timings, embeddingMs: encoded.timings.embeddingMs, totalMs: performance.now() - started };
  metrics.push({ type: 'query', collection, text, mode, runtime, cacheHits: encoded.cacheHits,
    timings: result.timings, at: new Date().toISOString() });
  $('time-embed').textContent = ms(result.timings.embeddingMs);
  $('time-search').textContent = ms(result.timings.searchMs);
  $('time-total').textContent = ms(result.timings.totalMs);
  status(`在 ${collection} 找到 ${result.results.length} 条结果。`);
  return result;
}
async function createCollectionInternal(name) {
  await api('/collections', { name });
  selected = name;
  $('results').replaceChildren();
  await updateCollections();
  status(`已切换到知识库 ${name}`);
}
const kb = {
  model: MODEL, metrics,
  get runtime() { return runtime; },
  get collection() { return selected; },
  initialize: options => exclusive(() => initializeInternal(options)),
  ingest: (documents, options) => exclusive(() => ingestInternal(documents, options)),
  search: (text, options) => exclusive(() => searchInternal(text, options)),
  createCollection: name => exclusive(() => createCollectionInternal(name)),
  selectCollection: name => exclusive(async () => {
    const collections = await api('/collections');
    if (!collections.some(c => c.name === name)) throw new Error('知识库不存在');
    selected = name; $('results').replaceChildren(); await updateCollections();
  }),
  refresh: () => exclusive(async () => {
    const result = await api(`/collections/${selected}/refresh`, {});
    await updateCollections(); status('索引已刷新。'); return result;
  }),
};
window.kb = kb;
const ui = fn => event => { event?.preventDefault(); Promise.resolve().then(fn).catch(error => status(error.message, true)); };
$('import').onclick = ui(async () => {
  const files = [...$('files').files];
  const documents = [];
  for (const file of files) {
    if (file.size > 2 * 1024 * 1024) throw new Error(`${file.name} 超过 2 MB`);
    documents.push({ source: file.name, text: await file.text() });
  }
  if ($('text').value.trim()) documents.push({ source: $('source').value.trim() || '粘贴的笔记', text: $('text').value });
  await kb.ingest(documents, { batchSize: Number($('batch').value) });
  $('files').value = ''; $('text').value = '';
});
$('demo').onclick = ui(async () => {
  const data = await fetch('/data/evaluation.json').then(r => r.json());
  await kb.ingest(data.documents.map(d => ({ source: d.id, text: d.text })), { batchSize: Number($('batch').value) });
});
$('search-form').onsubmit = ui(() => kb.search($('query').value.trim(), { mode: document.querySelector('input[name="mode"]:checked').value }));
$('create-form').onsubmit = ui(async () => { await kb.createCollection($('collection-name').value.trim()); $('collection-name').value = ''; });
$('collection').onchange = ui(() => kb.selectCollection($('collection').value));
$('refresh').onclick = ui(() => kb.refresh());
$('export').onclick = () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify({ model: MODEL, browser: navigator.userAgent, metrics }, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = 'knowledge-timings.json'; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
};
kb.ready = exclusive(async () => {
  const health = await api('/health');
  $('db-status').textContent = '● seekdb 已连接';
  $('db-status').title = health.version;
  await updateCollections();
}).catch(error => { $('db-status').textContent = 'seekdb 连接失败'; throw error; });
