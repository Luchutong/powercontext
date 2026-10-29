export class Embedder {
  constructor(onProgress = () => {}) {
    this.worker = new Worker(new URL('./embedding.worker.mjs', import.meta.url), { type: 'module' });
    this.pending = new Map();
    this.nextId = 0;
    this.onProgress = onProgress;
    this.worker.onmessage = ({ data }) => {
      const pending = this.pending.get(data.id);
      if (!pending) return;
      if (data.progress) { this.onProgress(data.progress); return; }
      this.pending.delete(data.id);
      if (data.error) pending.reject(new Error(data.error));
      else pending.resolve(data.result);
    };
    this.worker.onerror = error => {
      for (const { reject } of this.pending.values()) reject(new Error(error.message));
      this.pending.clear();
    };
  }
  call(type, options) {
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({ id, type, options });
    });
  }
  initialize(options = {}) { return this.call('init', options); }
  embed(texts, options = {}) { return this.call('embed', { texts, ...options }); }
}
