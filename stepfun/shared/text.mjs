// Keep chunks below the tokenizer limit even for Chinese text. Retain source offsets.
export function splitDocument(text, { size = 180, overlap = 30 } = {}) {
  if (!Number.isInteger(size) || !Number.isInteger(overlap) || size < 1 || overlap < 0 || overlap >= size) {
    throw new Error('Invalid chunk size or overlap');
  }
  const chars = Array.from(text.replace(/\r\n?/g, '\n'));
  const chunks = [];
  for (let start = 0; start < chars.length;) {
    let end = Math.min(start + size, chars.length);
    if (end < chars.length) {
      for (let i = end; i > start + size * 0.65; i--) {
        if (/[。！？.!?\n]/u.test(chars[i - 1])) { end = i; break; }
      }
    }
    const content = chars.slice(start, end).join('');
    if (content.trim()) chunks.push({ text: content, start, end, chunk: chunks.length });
    if (end === chars.length) break;
    start = Math.max(start + 1, end - overlap);
  }
  return chunks;
}

export function qualityMetrics(ids, relevant, k = 5) {
  const expected = new Set(relevant);
  const top = [...new Set(ids)].slice(0, k);
  const hits = top.filter(id => expected.has(id)).length;
  const dcg = top.reduce((s, id, i) => s + (expected.has(id) ? 1 / Math.log2(i + 2) : 0), 0);
  const ideal = Array.from({ length: Math.min(k, expected.size) }, (_, i) => 1 / Math.log2(i + 2)).reduce((a, b) => a + b, 0);
  return { recall: expected.size ? hits / expected.size : 0, ndcg: ideal ? dcg / ideal : 0 };
}
