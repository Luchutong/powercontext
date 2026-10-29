import assert from 'node:assert/strict';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdir, writeFile } from 'node:fs/promises';
import { openBrowser } from './browser.mjs';

const container = process.env.SEEKDB_TEST_CONTAINER;
if (!container || !/^[a-zA-Z0-9][a-zA-Z0-9_.-]+$/.test(container)) {
  throw new Error('Set SEEKDB_TEST_CONTAINER to the disposable Docker database container to restart.');
}
await mkdir('test-results', { recursive: true });
const run = await openBrowser();
try {
  const { page } = run;
  const collection = `restart_${Date.now().toString(36)}`;
  const original = { source: 'original-note', text: '火星探测车研究古代河流遗迹，帮助科学家了解这颗行星过去的气候。' };
  await page.evaluate(name => window.kb.createCollection(name), collection);
  await page.evaluate(doc => window.kb.ingest([doc]), original);
  const before = await page.evaluate(() => window.kb.search('怎样研究火星古代气候'));
  assert.equal(before.results[0].metadata.source, original.source);
  await promisify(execFile)('docker', ['restart', container], { timeout: 180000 });
  // Keep this browser and the Node service running, exercising the stale SDK
  // connection. Recreating the app here would hide the original defect.
  const after = await page.evaluate(() => window.kb.search('怎样研究火星古代气候'));
  assert.equal(after.results[0].id, before.results[0].id);
  const repeated = await page.evaluate(doc => window.kb.ingest([doc]), original);
  assert.equal(repeated.count, 1);
  const imported = await page.evaluate(() => window.kb.ingest([
    { source: 'new-note', text: '木星的大红斑是一场持续多年的巨大风暴，探测器通过观测云层了解其大气变化。' },
  ]));
  assert.equal(imported.count, 2);
  const fresh = await page.evaluate(() => window.kb.search('木星的大红斑是什么'));
  assert.equal(fresh.results[0].metadata.source, 'new-note');
  assert.deepEqual(run.errors, []);
  assert.deepEqual(run.remoteRequests, []);
  await writeFile('test-results/restart.json', JSON.stringify({ at: new Date().toISOString(),
    collection, environment: run.environment, before, after, repeated, imported, fresh,
    checks: ['persistent-original', 'live-sdk-reconnect', 'idempotent-retry', 'incremental-after-restart'],
  }, null, 2) + '\n');
  console.log('Live database restart: reconnect, persistence, duplicate retry and new imports passed');
} finally { await run.browser.close(); }
