import express from 'express';
import { fileURLToPath } from 'node:url';
import { createStore } from './store.mjs';
import { MODEL } from '../shared/model.mjs';
import { collectionName, importRecords, InputError, signature, vector } from './validation.mjs';

const root = fileURLToPath(new URL('../', import.meta.url));
try { process.loadEnvFile(`${root}/.env`); } catch (error) { if (error.code !== 'ENOENT') throw error; }
const app = express();
const port = Number(process.env.PORT || 4177);
const host = process.env.HOST || '127.0.0.1';
app.use((req, res, next) => {
  // A local-only application: reject cross-origin writes and DNS rebinding.
  const expectedHost = `${host === '0.0.0.0' ? req.hostname : host}:${port}`;
  if (host !== '0.0.0.0' && ![expectedHost, `localhost:${port}`].includes(req.headers.host)) return res.sendStatus(403);
  const origin = req.headers.origin;
  if (origin && origin !== `http://${req.headers.host}` && origin !== `https://${req.headers.host}`) return res.sendStatus(403);
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
  next();
});
app.use(express.json({ limit: '8mb' }));
app.use('/ort', express.static(`${root}/node_modules/onnxruntime-web/dist`));
const store = await createStore();
await store.create('personal');
app.get('/api/health', (_req, res) => res.json({ ok: true, version: store.version, model: MODEL }));
app.get('/api/collections', async (_req, res) => res.json(await store.list()));
app.post('/api/collections', async (req, res) => {
  collectionName(req.body.name);
  res.json(await store.create(req.body.name));
});
app.post('/api/collections/:name/documents', async (req, res) => {
  collectionName(req.params.name);
  const records = importRecords(req.body);
  if (req.body.refresh !== undefined && typeof req.body.refresh !== 'boolean') throw new InputError('refresh 须为布尔值');
  res.json(await store.write(req.params.name, records, req.body.refresh !== false));
});
app.post('/api/collections/:name/refresh', async (req, res) => {
  collectionName(req.params.name);
  res.json(await store.refresh(req.params.name));
});
app.post('/api/collections/:name/search', async (req, res) => {
  collectionName(req.params.name);
  signature(req.body.signature);
  const { text, k = 5, mode = 'hybrid', embedding } = req.body;
  if (typeof text !== 'string' || !text.trim() || text.length > 500 ||
      !Number.isInteger(k) || k < 1 || k > 50 || !['hybrid', 'vector'].includes(mode)) throw new InputError('无效的查询、结果数量或检索模式');
  vector(embedding);
  res.json(await store.search(req.params.name, { text, embedding, k, mode }));
});
app.use('/api', (_req, res) => res.status(404).json({ error: '接口不存在' }));
let vite;
if (process.env.NODE_ENV === 'production') {
  app.use('/models', express.static(`${root}/public/models`));
  app.use(express.static(`${root}/dist`));
} else {
  const { createServer } = await import('vite');
  vite = await createServer({ root, server: { middlewareMode: true }, appType: 'spa' });
  app.use(vite.middlewares);
}
app.use((error, _req, res, _next) => {
  console.error(error);
  res.status(error.status || 500).json({ error: error.status ? error.message : `seekdb 操作失败：${error.message}` });
});
const server = app.listen(port, host, () => console.log(`Knowledge base: http://${host}:${port} · ${store.version}`));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, async () => {
  server.close();
  await vite?.close();
  await store.close();
  process.exit(0);
});
