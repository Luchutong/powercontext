import test from 'node:test';
import assert from 'node:assert/strict';
import { splitDocument, qualityMetrics } from '../shared/text.mjs';
import { MODEL } from '../shared/model.mjs';
import { collectionName, importRecords, SerialQueue, vector } from '../server/validation.mjs';
import { backendCandidates } from '../src/backend.mjs';

test('chunking preserves Unicode, normalized offsets, complete coverage and overlap', () => {
  const text = '甲😀乙。'.repeat(200) + '\r\n结尾';
  const normalized = Array.from(text.replace(/\r\n?/g, '\n'));
  const chunks = splitDocument(text);
  assert.equal(chunks[0].start, 0);
  assert.equal(chunks.at(-1).end, normalized.length);
  chunks.forEach((c, i) => {
    assert.equal(c.text, normalized.slice(c.start, c.end).join(''));
    assert.ok(Array.from(c.text).length <= 180);
    assert.equal(c.chunk, i);
    if (i) { assert.equal(c.start, chunks[i - 1].end - 30); assert.ok(c.end > chunks[i - 1].end); }
  });
  assert.deepEqual(splitDocument('  \n'), []);
  assert.throws(() => splitDocument('a', { size: 3, overlap: 3 }));
});
test('ranking metrics penalize late hits and do not count duplicate chunks as documents', () => {
  assert.deepEqual(qualityMetrics(['a', 'a', 'b'], ['a', 'b'], 2), { recall: 1, ndcg: 1 });
  assert.equal(qualityMetrics(['x', 'a'], ['a'], 1).recall, 0);
  assert.ok(qualityMetrics(['x', 'a'], ['a'], 2).ndcg < 1);
});
test('GPU preference, missing adapters, probe rejection, precision and explicit WASM', async () => {
  const gpu = { requestAdapter: async () => ({ features: new Set(['shader-f16']) }) };
  assert.deepEqual((await backendCandidates({}, gpu)).map(c => [c.device, c.dtype]), [['webgpu', 'fp16'], ['wasm', 'q8']]);
  assert.equal((await backendCandidates({}, { requestAdapter: async () => null }))[0].device, 'wasm');
  assert.equal((await backendCandidates({}, { requestAdapter: async () => ({ features: new Set() }) }))[0].device, 'wasm');
  assert.equal((await backendCandidates({ dtype: 'q8' }, gpu))[0].device, 'wasm');
  assert.equal((await backendCandidates({}, { requestAdapter: async () => { throw Error('denied'); } }))[0].device, 'wasm');
  assert.equal((await backendCandidates({ device: 'wasm', dtype: 'fp32' }, gpu))[0].dtype, 'fp32');
  await assert.rejects(backendCandidates({ dtype: 'typo' }, gpu));
});
test('API rejects mixed models, invalid identifiers, malformed vectors and chunks', () => {
  assert.equal(collectionName('notes_1'), 'edge_v1_notes_1');
  for (const name of ['../x', 'x; DROP TABLE t', '', 'A', 'edge-1']) assert.throws(() => collectionName(name));
  const validVector = [1, ...new Array(511).fill(0)];
  vector(validVector);
  for (const value of [[1], new Array(512).fill(0), [NaN, ...validVector.slice(1)]]) assert.throws(() => vector(value));
  const record = { id: 'a'.repeat(64), text: '正文', source: 'doc', documentId: 'doc', chunk: 0, start: 0, end: 2, embedding: validVector };
  assert.equal(importRecords({ signature: MODEL.signature, records: [record] }).length, 1);
  assert.throws(() => importRecords({ signature: 'different', records: [record] }));
  assert.throws(() => importRecords({ signature: MODEL.signature, records: [{ ...record, chunk: -1 }] }));
});
test('SDK session sequences remain ordered and queue recovers from a failed request', async () => {
  const queue = new SerialQueue();
  const events = [];
  const a = queue.run(async () => { events.push('set A'); await Promise.resolve(); events.push('select A'); throw Error('A failed'); });
  const b = queue.run(async () => { events.push('set B'); await Promise.resolve(); events.push('select B'); return 'B'; });
  await assert.rejects(a, /A failed/);
  assert.equal(await b, 'B');
  assert.deepEqual(events, ['set A', 'select A', 'set B', 'select B']);
});
