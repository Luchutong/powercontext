import { mkdir, readFile, rename, stat, writeFile } from 'node:fs/promises';
import { createReadStream, createWriteStream } from 'node:fs';
import { createHash } from 'node:crypto';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { fileURLToPath } from 'node:url';
import { MODEL, MODEL_FILES } from '../shared/model.mjs';

const root = fileURLToPath(new URL('../public/models/bge-small-zh-v1.5/', import.meta.url));
const base = `https://huggingface.co/${MODEL.id}/resolve/${MODEL.revision}/`;
const files = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json',
  'vocab.txt', 'quantize_config.json', ...Object.values(MODEL_FILES)];
await mkdir(`${root}/onnx`, { recursive: true });
let previous;
try { previous = JSON.parse(await readFile(`${root}/manifest.json`, 'utf8')); } catch {}
const manifest = { model: MODEL.id, revision: MODEL.revision, files: {} };
for (const file of files) {
  const path = `${root}/${file}`;
  let size = 0;
  try { size = (await stat(path)).size; } catch {}
  const hashFile = async () => {
    const hash = createHash('sha256');
    for await (const bytes of createReadStream(path)) hash.update(bytes);
    return hash.digest('hex');
  };
  if (!size || previous?.model !== MODEL.id || previous?.revision !== MODEL.revision ||
      !previous?.files[file] || previous.files[file].sha256 !== await hashFile()) {
    console.log(`Downloading ${file} (${MODEL.revision})`);
    const response = await fetch(base + file);
    if (!response.ok) throw new Error(`${file}: HTTP ${response.status}`);
    await pipeline(Readable.fromWeb(response.body), createWriteStream(`${path}.partial`));
    const expected = Number(response.headers.get('content-length'));
    size = (await stat(`${path}.partial`)).size;
    if (expected && size !== expected) throw new Error(`Incomplete download: ${file}`);
    await rename(`${path}.partial`, path);
  }
  manifest.files[file] = { bytes: size, sha256: await hashFile(), source: base + file };
}
await writeFile(`${root}/manifest.json`, JSON.stringify(manifest, null, 2) + '\n');
const sizes = Object.fromEntries(Object.entries(MODEL_FILES).map(([k, v]) => [k, manifest.files[v].bytes]));
console.log(JSON.stringify({ sizes, compression: { q8: sizes.fp32 / sizes.q8, fp16: sizes.fp32 / sizes.fp16 } }, null, 2));
