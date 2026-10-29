export async function backendCandidates({ device = 'auto', dtype = 'auto' } = {}, gpu = globalThis.navigator?.gpu) {
  if (!['auto', 'wasm', 'webgpu'].includes(device) || !['auto', 'fp32', 'fp16', 'q8'].includes(dtype)) {
    throw new Error('Invalid inference configuration');
  }
  let adapter;
  if (device !== 'wasm' && gpu) {
    try { adapter = await gpu.requestAdapter(); } catch {}
  }
  const fallback = { device: 'wasm', dtype: dtype === 'fp32' ? 'fp32' : 'q8' };
  // Dynamic INT8 MatMul on the pinned ORT WebGPU backend produced invalid semantic
  // vectors on SwiftShader. Use FP16 on compatible GPUs, and INT8 on WASM otherwise.
  // FP32 is exposed for controlled baseline measurements only.
  if (adapter && dtype === 'auto' && !adapter.features.has('shader-f16')) {
    return [{ ...fallback, reason: 'WebGPU 缺少 shader-f16，已自动使用 WASM / INT8' }];
  }
  if (adapter && dtype === 'q8') return [{ ...fallback, reason: 'INT8 使用经过质量验证的 WASM 后端' }];
  if (adapter) return [{ device: 'webgpu', dtype: dtype === 'auto' ? 'fp16' : dtype }, fallback];
  return [{ ...fallback, reason: device === 'wasm' ? '已指定 WASM 测试模式' : 'WebGPU 不可用，已自动使用 WASM' }];
}
