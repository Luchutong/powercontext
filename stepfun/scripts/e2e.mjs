import assert from 'node:assert/strict';
import { mkdir, writeFile } from 'node:fs/promises';
import { openBrowser, baseURL } from './browser.mjs';
import { MODEL } from '../shared/model.mjs';

await mkdir('test-results', { recursive: true });
const reports = [];
for (const config of [{ gpu: false }, { gpu: true }, { gpu: true, fp32: true }]) {
  const { gpu, fp32 } = config;
  const run = await openBrowser({ gpu });
  const { page, browser, errors, remoteRequests } = run;
  try {
    const suffix = Date.now().toString(36);
    const nameA = `e2e_${suffix}_a`, nameB = `e2e_${suffix}_b`;
    let initializationFallback;
    if (gpu && run.environment.gpu && !run.environment.gpu.features.includes('shader-f16')) {
      const failedFp16 = await page.evaluate(() => window.kb.initialize({ device: 'webgpu', dtype: 'fp16' }));
      assert.equal(failedFp16.device, 'wasm');
      assert.match(failedFp16.reason, /失败/);
      initializationFallback = failedFp16;
    }
    const runtime = await page.evaluate(fp32 => window.kb.initialize(fp32 ? { device: 'webgpu', dtype: 'fp32' } : {}), fp32);
    assert.equal(runtime.device, gpu && run.environment.gpu && (fp32 || run.environment.gpu.features.includes('shader-f16')) ? 'webgpu' : 'wasm');
    assert.match(await page.locator('#backend').innerText(), new RegExp(runtime.device, 'i'));
    if (!gpu) assert.match(runtime.reason, /不可用/);
    await page.locator('#collection-name').fill(nameA);
    await page.locator('#create-form button').click();
    await page.waitForFunction(name => window.kb.collection === name && !document.querySelector('#import').disabled, nameA);
    const original = '新建资料验证：北极狐的毛色在冬天变为白色，便于在积雪中隐藏。';
    await page.locator('#files').setInputFiles({ name: 'arctic.txt', mimeType: 'text/plain', buffer: Buffer.from(original) });
    await page.locator('#import').click();
    await page.waitForFunction(() => document.querySelector('#status').textContent.includes('索引已刷新'));
    assert.equal(await page.locator('#count').innerText(), '1');
    const duplicate = await page.evaluate(text => window.kb.ingest([{ source: 'arctic.txt', text }]), original);
    assert.equal(duplicate.count, 1);
    const incremental = await page.evaluate(() => window.kb.ingest([
      { source: 'whale.md', text: '鲸鱼是海洋哺乳动物，用肺呼吸，需要定期浮到水面。鲸鱼幼崽通过母乳获得营养。' },
      { source: 'unsafe-title', text: '<script>alert("xss")</script> 应作为普通文档原文展示。' },
    ]));
    assert.equal(incremental.count, 3);
    // Query through visible UI: asserts freshly imported content is searchable.
    await page.locator('#query').fill('北极狐冬季为什么变白');
    await page.locator('#search-form button').click();
    await page.waitForFunction(() => document.querySelector('#results').textContent.includes('arctic.txt'));
    assert.match(await page.locator('.result').first().innerText(), /北极狐/);
    await page.waitForFunction(() => !document.querySelector('#import').disabled);
    await page.evaluate(name => window.kb.createCollection(name), nameB);
    const empty = await page.evaluate(() => window.kb.search('北极狐冬季为什么变白'));
    assert.equal(empty.results.length, 0);
    await page.evaluate(() => window.kb.ingest([{ source: 'fruit', text: '红富士苹果是一种口感脆甜的水果，含有膳食纤维。' }]));
    const isolated = await page.evaluate(() => window.kb.search('苹果'));
    assert.ok(isolated.results.every(r => r.metadata.source === 'fruit'));
    await page.evaluate(name => window.kb.selectCollection(name), nameA);
    // Two calls arrive on one SDK session concurrently. They must not exchange query state.
    const data = await page.evaluate(() => fetch('/data/evaluation.json').then(r => r.json()));
    await page.evaluate(docs => window.kb.ingest(docs.map(d => ({ source: d.id, text: d.text }))), data.documents);
    const searchInputs = await page.evaluate(() => {
      // Reuse vectors sent by the real browser for an HTTP concurrency assertion.
      const savedFetch = window.fetch;
      const bodies = [];
      window.fetch = async (url, init) => {
        if (String(url).endsWith('/search')) bodies.push({ url, body: JSON.parse(init.body) });
        return savedFetch(url, init);
      };
      return (async () => {
        try {
          await window.kb.search('番茄鸡蛋面怎么做');
          await window.kb.search('绿茶冲泡水温');
          return bodies;
        } finally { window.fetch = savedFetch; }
      })();
    });
    const responses = await Promise.all(searchInputs.map(({ url, body }) => fetch(baseURL + url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }).then(r => r.json())));
    assert.equal(responses[0].results[0].metadata.source, 'tomato-noodles');
    assert.equal(responses[1].results[0].metadata.source, 'tea-brewing');
    // Reload restores the selected collection, not a new empty in-memory database.
    await page.reload(); await page.waitForFunction(() => window.kb); await page.evaluate(() => window.kb.ready);
    assert.equal(await page.evaluate(() => window.kb.collection), nameA);
    assert.equal(await page.locator('#count').innerText(), '35');
    await page.evaluate(() => window.kb.search('怎样同时利用关键词和语义查找资料？'));
    await page.screenshot({ path: `test-results/${fp32 ? 'webgpu-fp32' : gpu ? 'auto' : 'wasm'}.png`, fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.deepEqual(errors, []);
    assert.deepEqual(remoteRequests, []);
    reports.push({ path: fp32 ? 'gpu-fp32-validation' : gpu ? 'gpu-preferred' : 'gpu-unavailable', environment: run.environment,
      runtime, initializationFallback, checks: ['file-import', 'incremental', 'idempotence', 'fresh-index', 'hybrid',
        'collection-isolation', 'concurrent-search', 'reload-persistence', 'mobile-layout', 'no-remote-requests'] });
    console.log(`${runtime.device}/${runtime.dtype}: end-to-end checks passed`);
  } finally { await browser.close(); }
}
// Malformed vectors and incompatible models are rejected before touching the database.
for (const body of [{ signature: MODEL.signature, text: 'test', embedding: [1] },
  { signature: 'wrong-model', text: 'test', embedding: Array(512).fill(0) }]) {
  const response = await fetch(`${baseURL}/api/collections/personal/search`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  assert.equal(response.status, 400);
}
const forbidden = await fetch(`${baseURL}/api/collections`, {
  method: 'POST', headers: { Origin: 'https://example.org', 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'bad_origin' }),
});
assert.equal(forbidden.status, 403);
await writeFile('test-results/e2e.json', JSON.stringify({ at: new Date().toISOString(), reports }, null, 2) + '\n');
console.log('API validation and origin checks passed');
