import { mkdir, rename, rm, stat } from 'node:fs/promises';
import { createReadStream, createWriteStream } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, basename } from 'node:path';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { fetch, EnvHttpProxyAgent } from 'undici';

let dispatcher;
export function downloadFetch(url, options = {}) {
  // Node's global fetch ignores HTTP(S)_PROXY on Node 22 and in default Node 24.
  // Match npm/curl's proxy environment, including NO_PROXY for local mirrors.
  dispatcher ??= new EnvHttpProxyAgent();
  return fetch(url, { ...options, dispatcher });
}
export async function closeDownloads() { await dispatcher?.close(); dispatcher = undefined; }
export async function sha256(path) {
  const hash = createHash('sha256');
  for await (const bytes of createReadStream(path)) hash.update(bytes);
  return hash.digest('hex');
}
export async function validAsset(path, expected) {
  try { return (await stat(path)).size === expected.bytes && await sha256(path) === expected.sha256; }
  catch (error) { if (error.code === 'ENOENT') return false; throw error; }
}
export async function downloadAsset(url, path, expected, {
  fetchImpl = downloadFetch, attempts = 3, retryDelay = 500, timeoutMs = 180000,
} = {}) {
  await mkdir(dirname(path), { recursive: true });
  let failure;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    const partial = `${path}.partial`;
    try {
      const response = await fetchImpl(url, { signal: AbortSignal.timeout(timeoutMs) });
      if (!response.ok) {
        await response.body?.cancel();
        throw new Error(`HTTP ${response.status}`);
      }
      await pipeline(Readable.fromWeb(response.body), createWriteStream(partial));
      if (!await validAsset(partial, expected)) throw new Error('File size or SHA-256 does not match the pinned model');
      await rename(partial, path);
      return;
    } catch (error) {
      failure = error;
      await rm(partial, { force: true });
      if (attempt < attempts) await new Promise(resolve => setTimeout(resolve, retryDelay * attempt));
    }
  }
  throw new Error(`Cannot download ${basename(path)} after ${attempts} attempts: ${failure.message}`, { cause: failure });
}
