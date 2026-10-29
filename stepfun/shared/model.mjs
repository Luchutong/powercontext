export const MODEL = Object.freeze({
  id: 'Xenova/bge-small-zh-v1.5',
  revision: '75c43b069aac4d136ba6bc1122f995fedcfd2781',
  path: '/models/bge-small-zh-v1.5/',
  dimension: 512,
  maxLength: 256,
  signature: 'bge-small-zh-v1.5@75c43b0:cls:l2:256:v1',
  queryPrefix: '为这个句子生成表示以用于检索相关文章：',
});

export const MODEL_FILES = {
  fp32: 'onnx/model.onnx',
  fp16: 'onnx/model_fp16.onnx',
  q8: 'onnx/model_quantized.onnx',
};
