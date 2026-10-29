import { MODEL } from '../shared/model.mjs';

export class InputError extends Error { status = 400; }
export function collectionName(value) {
  if (typeof value !== 'string' || !/^[a-z][a-z0-9_]{0,39}$/.test(value)) {
    throw new InputError('知识库名称须为 1–40 位小写字母、数字或下划线，并以字母开头');
  }
  return `edge_v1_${value}`;
}
export function vector(value) {
  if (!Array.isArray(value) || value.length !== MODEL.dimension || !value.every(Number.isFinite)) {
    throw new InputError(`向量须包含 ${MODEL.dimension} 个有限数值`);
  }
  const norm = Math.hypot(...value);
  if (norm < 0.99 || norm > 1.01) throw new InputError('向量必须经过 L2 归一化');
  return value;
}
export function signature(value) {
  if (value !== MODEL.signature) throw new InputError('模型版本与知识库不一致，请重新加载应用');
}
export function importRecords(body) {
  signature(body.signature);
  if (!Array.isArray(body.records) || !body.records.length || body.records.length > 128) {
    throw new InputError('每次写入须包含 1–128 个文本块');
  }
  return body.records.map(record => {
    if (!record || typeof record.id !== 'string' || !/^[a-f0-9]{64}$/.test(record.id) ||
        typeof record.text !== 'string' || !record.text.trim() || record.text.length > 4000 ||
        typeof record.source !== 'string' || !record.source.trim() || record.source.length > 512 ||
        typeof record.documentId !== 'string' || record.documentId.length > 128 ||
        !Number.isInteger(record.chunk) || record.chunk < 0 ||
        !Number.isInteger(record.start) || record.start < 0 ||
        !Number.isInteger(record.end) || record.end <= record.start) {
      throw new InputError('无效的文档 ID、原文或来源元数据');
    }
    vector(record.embedding);
    return record;
  });
}

// HybridSearch uses session variables on one SDK connection. Serialize whole operations,
// including SET + GET_SQL + SELECT, so concurrent requests cannot exchange query state.
export class SerialQueue {
  tail = Promise.resolve();
  run(operation) {
    const result = this.tail.then(operation);
    this.tail = result.catch(() => {});
    return result;
  }
}
