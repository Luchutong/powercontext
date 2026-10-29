import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { MODEL, MODEL_FILES } from '../shared/model.mjs';
import { validAsset, downloadAsset, closeDownloads } from './model-assets.mjs';

const root = fileURLToPath(new URL('../public/models/bge-small-zh-v1.5/', import.meta.url));
const base = `https://huggingface.co/${MODEL.id}/resolve/${MODEL.revision}/`;
const integrity = JSON.parse(await readFile(new URL('../shared/model-integrity.json', import.meta.url), 'utf8'));
if (integrity.model !== MODEL.id || integrity.revision !== MODEL.revision) throw new Error('Model integrity lock does not match the configured revision');
const runtimeOnly = process.argv.includes('--runtime-only');
if (process.argv.slice(2).some(arg => arg !== '--runtime-only')) throw new Error('Usage: npm run models [-- --runtime-only]');
const files = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json',
  'vocab.txt', 'quantize_config.json', ...(runtimeOnly ? [MODEL_FILES.q8, MODEL_FILES.fp16] : Object.values(MODEL_FILES))];
await mkdir(`${root}/onnx`, { recursive: true });
const manifest = { model: MODEL.id, revision: MODEL.revision, files: {} };
try {
  for (const file of files) {
    const path = `${root}/${file}`, expected = integrity.files[file];
    if (!await validAsset(path, expected)) {
      console.log(`Downloading ${file} (${MODEL.revision})`);
      await downloadAsset(base + file, path, expected);
    }
    manifest.files[file] = { ...expected, source: base + file };
  }
} finally { await closeDownloads(); }
// Keep already cached comparison files in the manifest when starting in runtime-only mode.
for (const file of Object.values(MODEL_FILES)) if (!manifest.files[file] && await validAsset(`${root}/${file}`, integrity.files[file])) {
  manifest.files[file] = { ...integrity.files[file], source: base + file };
}
await writeFile(`${root}/manifest.json`, JSON.stringify(manifest, null, 2) + '\n');
const sizes = Object.fromEntries(Object.entries(MODEL_FILES).filter(([, file]) => manifest.files[file]).map(([k, v]) => [k, manifest.files[v].bytes]));
console.log(JSON.stringify({ sizes, ...(sizes.fp32 ? { compression: { q8: sizes.fp32 / sizes.q8, fp16: sizes.fp32 / sizes.fp16 } } : {}) }, null, 2));
