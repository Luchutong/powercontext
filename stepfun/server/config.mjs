import { fileURLToPath } from 'node:url';
export const root = fileURLToPath(new URL('../', import.meta.url));
export function loadEnvironment() {
  try { process.loadEnvFile(`${root}/.env`); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
}
export function connectionConfig(env = process.env) {
  const port = Number(env.SEEKDB_PORT || 2883);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('SEEKDB_PORT must be between 1 and 65535');
  const database = env.SEEKDB_DATABASE || 'stepfun_edge';
  if (!/^[a-zA-Z][a-zA-Z0-9_]{0,63}$/.test(database)) throw new Error('Invalid SEEKDB_DATABASE');
  return { host: env.SEEKDB_HOST || '127.0.0.1', port, user: env.SEEKDB_USER || 'root',
    password: env.SEEKDB_PASSWORD || '', database, queryTimeout: 60000 };
}
