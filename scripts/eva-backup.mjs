import { config } from '../src/config.js';
import { backupData } from '../src/db.js';

try {
  await backupData(`${config.root}/data/backups`);
  process.exit(0);
} catch (error) {
  console.error(String(error?.message || error));
  process.exit(1);
}
