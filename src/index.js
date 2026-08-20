// Eva's processtart. Adapters leveren context; alle taal, tools en antwoorden
// lopen door src/mind/agent.js.
import fs from 'node:fs';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { config } from './config.js';
import {
  dailyBackupDue,
  logTimeline,
  maintain,
} from './db.js';
import { authStatus } from './brain/oauth.js';
import { cachedStates, connectHA } from './integrations/homeassistant.js';
import { startTelegram } from './integrations/telegram.js';
import { startWeb } from './web/server.js';
import { startVoiceBridge } from './mind/voicebridge.js';
import { startReminderLoop } from './reminders.js';
import { startAutonomy } from './mind/autonomy.js';
import { startPerception } from './mind/perception.js';
import { startEmotionalLife } from './mind/emotions.js';
import { startCognitiveCore } from './mind/cognitive.js';
import {
  setPersonHouseholdRole,
  syncHouseholdRolesFromPresence,
} from './mind/household.js';
import { runtimeVersion } from './runtimeversion.js';
import { registerVaultSecretsForRedaction } from './self/vault.js';

console.log(`
  Eva start:
  input -> one Eva -> persistent work cells -> bounded parallel workers -> verification -> one reply
`);

fs.mkdirSync(config.workspaceDir, { recursive: true });
registerVaultSecretsForRedaction();

// Start de gedeelde episode- en aandachtstoestand voordat sensoren gebeurtenissen
// kunnen uitsturen. Adapters blijven zintuigen/uitgangen, niet losse Eva's.
startCognitiveCore();

const liveVersion = runtimeVersion();
logTimeline('system', `Eva is opgestart met kern ${liveVersion.loaded_code_sha256.slice(0, 12)}.`, {
  internalOnly: true,
  startup: true,
  runtimeVersion: liveVersion,
});

const auth = authStatus();
if (auth.status !== 'ok' && !config.openaiApiKey) {
  console.log('Let op: ChatGPT is nog niet gekoppeld. Open het dashboard om in te loggen.');
}

// Registreer de private camerapoort vóór HA live events kan uitsturen.
startPerception();

await startWeb();
console.log(`Dashboard: http://localhost:${config.webPort}`);

// De adapters starten geen eigen redeneerroute. HA vult alleen de actuele
// toestand en emits; Telegram en voice sturen hun beurt naar dezelfde agent.
await connectHA();
for (const name of config.householdMembers) {
  try {
    setPersonHouseholdRole(name, 'household_member', { source: 'configured_household' });
  } catch {
    logTimeline('system', `Geconfigureerd gezinslid ${name} bestaat nog niet als bekende persoon.`, {
      internalOnly: true,
      household: true,
    });
  }
}
if (config.trustHaPersonMembership) syncHouseholdRolesFromPresence(cachedStates());
startVoiceBridge();
await startTelegram();
startReminderLoop();
startEmotionalLife();
if (config.autonomyEnabled) startAutonomy();

maintain();
setInterval(maintain, 6 * 3600_000);

const backupDir = path.join(config.logicalDataDir, 'backups');
let backupProcess = null;
const runDailyBackupIfDue = () => {
  if (backupProcess || !dailyBackupDue(backupDir)) return;
  backupProcess = spawn(process.execPath, [`${config.root}/scripts/eva-backup.mjs`], {
    cwd: config.root,
    env: { ...process.env, EVA_DATA_DIR: config.logicalDataDir },
    stdio: 'ignore',
  });
  backupProcess.once('exit', () => { backupProcess = null; });
};
setTimeout(runDailyBackupIfDue, 15 * 60_000);
setInterval(runDailyBackupIfDue, 6 * 3600_000);

console.log('Eva draait met een enkele gespreks- en handelingslus.\n');

let shuttingDown = false;
const shutdown = (signal) => {
  if (shuttingDown) return;
  shuttingDown = true;
  try {
    logTimeline('system', `Eva is gestopt (${signal}).`, { internalOnly: true });
  } catch {
    // Het proces moet ook kunnen stoppen als de database hapert.
  }
  process.exit(0);
};
process.on('SIGINT', () => shutdown('SIGINT'));
process.on('SIGTERM', () => shutdown('SIGTERM'));
process.on('unhandledRejection', (error) => {
  console.error('Onverwachte fout:', error instanceof Error ? error.message : error);
});
process.on('uncaughtException', (error) => {
  console.error('Onverwachte fout:', error?.message || error);
  try {
    logTimeline('system', `Onverwachte fout: ${String(error?.message || error).slice(0, 200)}`, {
      internalOnly: true,
    });
  } catch {
    // De oorspronkelijke fout blijft leidend.
  }
  // Een onbekende exception kan gedeelde toestand beschadigd hebben. Laat
  // systemd een schone runtime starten in plaats van half kapot door te draaien.
  setTimeout(() => process.exit(1), 50);
});
