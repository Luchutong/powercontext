import { loadEnvironment, connectionConfig } from '../server/config.mjs';
import { waitForDatabase } from '../server/readiness.mjs';
loadEnvironment();
const config = connectionConfig();
await waitForDatabase(config);
console.log(`seekdb ready at ${config.host}:${config.port}`);
