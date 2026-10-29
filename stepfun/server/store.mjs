import { SeekdbClient, Schema, VectorIndexConfig, FulltextIndexConfig } from 'seekdb';
import { MODEL } from '../shared/model.mjs';
import { SerialQueue, collectionName, InputError } from './validation.mjs';
import { connectionConfig } from './config.mjs';
import { waitForDatabase } from './readiness.mjs';

export async function createStore({ database, ...config } = connectionConfig()) {
  const admin = new SeekdbClient({ ...config, database: 'test' });
  try { await admin.execute(`CREATE DATABASE IF NOT EXISTS \`${database}\``); }
  finally { await admin.close(); }
  const client = new SeekdbClient({ ...config, database });
  const [row] = await client.execute('SELECT VERSION() AS version');
  const version = row.version;
  if (!/seekdb/i.test(version)) throw new Error(`需要 seekdb，当前数据库：${version}`);
  const queue = new SerialQueue();
  const handles = new Map();
  const run = operation => queue.run(async () => {
    // The SDK keeps a closed MySQL connection after a server restart. Reconnect
    // before the next complete operation; never replay a possibly committed write.
    try { await client.execute('SELECT 1'); }
    catch {
      await client.close();
      handles.clear();
      await waitForDatabase(config);
    }
    return operation();
  });
  const get = async name => {
    const physical = collectionName(name);
    if (!handles.has(physical)) {
      if (!await client.hasCollection(physical)) {
        const error = new InputError('知识库不存在'); error.status = 404; throw error;
      }
      handles.set(physical, await client.getCollection({ name: physical, embeddingFunction: null }));
    }
    return handles.get(physical);
  };
  return {
    version,
    close: () => client.close(),
    list: () => run(async () => {
      const collections = await client.listCollections({ withEmbeddingFunction: false });
      const result = [];
      for (const item of collections.filter(c => c.name.startsWith('edge_v1_'))) {
        result.push({ name: item.name.slice(8), count: Number(await item.count()), signature: MODEL.signature });
      }
      return result.sort((a, b) => a.name.localeCompare(b.name));
    }),
    create: name => run(async () => {
      const physical = collectionName(name);
      if (!await client.hasCollection(physical)) {
        // Explicit null disables automatic embedding. This service has no inference runtime.
        const schema = new Schema()
          .createIndex(new VectorIndexConfig({ hnsw: { dimension: MODEL.dimension, distance: 'cosine' }, embeddingFunction: null }))
          .createIndex(new FulltextIndexConfig('ngram'));
        handles.set(physical, await client.createCollection({ name: physical, schema, embeddingFunction: null }));
      }
      return { name, count: Number(await (await get(name)).count()), signature: MODEL.signature };
    }),
    write: (name, records, refresh = true) => run(async () => {
      const collection = await get(name);
      const start = performance.now();
      await collection.upsert({
        ids: records.map(r => r.id), documents: records.map(r => r.text), embeddings: records.map(r => r.embedding),
        metadatas: records.map(r => ({ documentId: r.documentId, source: r.source, chunk: r.chunk,
          start: r.start, end: r.end, signature: MODEL.signature })),
      });
      const writeMs = performance.now() - start;
      const refreshStart = performance.now();
      if (refresh) await collection.refresh_index();
      return { written: records.length, count: Number(await collection.count()),
        timings: { writeMs, refreshMs: refresh ? performance.now() - refreshStart : 0 }, refreshed: refresh };
    }),
    refresh: name => run(async () => {
      const collection = await get(name);
      const start = performance.now();
      await collection.refresh_index();
      return { count: Number(await collection.count()), timings: { refreshMs: performance.now() - start }, refreshed: true };
    }),
    search: (name, { text, embedding, k, mode }) => run(async () => {
      const collection = await get(name);
      const start = performance.now();
      const options = { nResults: k, include: ['documents', 'metadatas', 'distances'] };
      const candidates = Math.max(20, k * 4);
      const result = mode === 'vector'
        ? await collection.query({ ...options, queryEmbeddings: [embedding] })
        : await collection.hybridSearch({ ...options,
          query: { whereDocument: { $contains: text }, nResults: candidates },
          knn: { queryEmbeddings: [embedding], nResults: candidates },
          rank: { rrf: { rankConstant: 60, rankWindowSize: candidates } },
        });
      return { mode, results: (result.ids?.[0] || []).map((id, i) => ({ id,
        text: result.documents?.[0]?.[i], metadata: result.metadatas?.[0]?.[i],
        score: result.distances?.[0]?.[i] ?? null })), timings: { searchMs: performance.now() - start } };
    }),
  };
}
