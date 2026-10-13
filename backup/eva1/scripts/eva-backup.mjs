import path from 'node:path';
import { config } from '../src/config.js';
import { backupData } from '../src/db.js';

try {
  await backupData(path.join(config.logicalDataDir, 'backups'));
  process.exit(0);
} catch (error) {
  console.error(String(error?.message || error));
  process.exit(1);
}
