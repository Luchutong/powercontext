import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { downloadAsset, validAsset, downloadFetch, closeDownloads } from '../scripts/model-assets.mjs';
import { connectionConfig } from '../server/config.mjs';
import { waitForDatabase } from '../server/readiness.mjs';

const lock = data => ({ bytes: Buffer.byteLength(data), sha256: createHash('sha256').update(data).digest('hex') });
test('interrupted model downloads retry; corrupt bodies cannot replace a verified file', async () => {
  const directory = await mkdtemp(`${tmpdir()}/stepfun-download-`);
  const target = `${directory}/weights.onnx`, contents = 'pinned model bytes';
  let attempts = 0;
  const server = createServer((_req, res) => {
    attempts++;
    if (attempts === 1) { res.writeHead(503); res.end('temporarily unavailable'); }
    else if (attempts === 2) { res.writeHead(200); res.end('truncated'); }
    else { res.writeHead(200); res.end(contents); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    await downloadAsset(`http://127.0.0.1:${server.address().port}/weights`, target, lock(contents), {
      fetchImpl: globalThis.fetch, retryDelay: 0,
    });
    assert.equal(attempts, 3);
    assert.equal(await validAsset(target, lock(contents)), true);
    assert.equal(await validAsset(target, lock('wrong bytes')), false);
    await assert.rejects(stat(`${target}.partial`), { code: 'ENOENT' });
    await assert.rejects(downloadAsset('http://unused/', target, lock(contents), {
      fetchImpl: async () => new Response('bad data'), attempts: 1,
    }), /SHA-256/);
    assert.equal(await readFile(target, 'utf8'), contents);
    await assert.rejects(stat(`${target}.partial`), { code: 'ENOENT' });
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await rm(directory, { recursive: true }); }
});

test('model downloader honors HTTP_PROXY without Node-specific command line flags', async () => {
  const server = createServer();
  let usedProxy = false;
  server.on('connect', (_req, socket) => {
    usedProxy = true;
    socket.write('HTTP/1.1 200 Connection Established\r\n\r\n');
    socket.once('data', () => socket.end('HTTP/1.1 200 OK\r\nContent-Length: 7\r\nConnection: close\r\n\r\nproxied'));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const previous = Object.fromEntries(['HTTP_PROXY', 'http_proxy', 'NO_PROXY', 'no_proxy'].map(key => [key, process.env[key]]));
  try {
    process.env.HTTP_PROXY = process.env.http_proxy = `http://127.0.0.1:${server.address().port}`;
    process.env.NO_PROXY = process.env.no_proxy = '';
    const response = await downloadFetch('http://model-download.invalid/config.json', { signal: AbortSignal.timeout(5000) });
    assert.equal(await response.text(), 'proxied');
    assert.equal(usedProxy, true);
  } finally {
    await closeDownloads();
    for (const [key, value] of Object.entries(previous)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; }
    server.closeAllConnections(); await new Promise(resolve => server.close(resolve));
  }
});

test('database readiness retries real connection failures and reports the configured endpoint', async () => {
  let attempts = 0, closed = 0;
  const config = connectionConfig({ SEEKDB_PORT: '2885', SEEKDB_DATABASE: 'fresh_start' });
  await waitForDatabase(config, { timeoutMs: 100, intervalMs: 1, createClient: options => {
    assert.equal(options.port, 2885); assert.equal(options.database, 'test');
    return { execute: async () => { attempts++; if (attempts < 3) throw Error('starting'); return [{ version: 'seekdb-v1.4.0.0' }]; }, close: async () => { closed++; } };
  } });
  assert.equal(attempts, 3); assert.equal(closed, 3);
  await assert.rejects(waitForDatabase(config, { timeoutMs: 0, createClient: () => ({
    execute: async () => { throw Error('not listening'); }, close: async () => {},
  }) }), /127.0.0.1:2885/);
  assert.throws(() => connectionConfig({ SEEKDB_PORT: 'not-a-port' }), /SEEKDB_PORT/);
  assert.throws(() => connectionConfig({ SEEKDB_DATABASE: 'x;DROP' }), /SEEKDB_DATABASE/);
});
