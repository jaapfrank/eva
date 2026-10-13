import fs from 'node:fs';
import { after } from 'node:test';

// Sommige zuivere suites hebben door hun statische dependencygraph toch
// config.js nodig. Geef ieder testbestand vóór die imports een eigen /tmp-root;
// een lege of onveilige bestaande waarde wordt bewust niet gemaskeerd.
if (!Object.hasOwn(process.env, 'EVA_DATA_DIR')) {
  const isolatedDataDir = fs.mkdtempSync('/tmp/bottie-static-test-data-');
  process.env.EVA_DATA_DIR = isolatedDataDir;
  after(() => fs.rmSync(isolatedDataDir, { recursive: true, force: true }));
}
