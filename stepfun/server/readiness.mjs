import { SeekdbClient } from 'seekdb';
export async function waitForDatabase(config, { timeoutMs = 180000, intervalMs = 1000, createClient = options => new SeekdbClient(options) } = {}) {
  const start = Date.now();
  let lastError;
  do {
    const client = createClient({ ...config, database: 'test' });
    try {
      const [row] = await client.execute('SELECT VERSION() AS version');
      if (!/seekdb/i.test(row.version)) throw new Error('The configured server is not seekdb');
      return;
    } catch (error) { lastError = error; }
    finally { await client.close(); }
    if (Date.now() - start >= timeoutMs) break;
    await new Promise(resolve => setTimeout(resolve, intervalMs));
  } while (Date.now() - start < timeoutMs);
  throw new Error(`seekdb is not ready at ${config.host}:${config.port}. Check SEEKDB_* in .env and the database startup log.`, { cause: lastError });
}
