import 'dotenv/config';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const env = process.env;
const dataDir = env.EVA_DATA_DIR ? path.resolve(env.EVA_DATA_DIR) : path.join(root, 'data');
const autonomyLevel = String(env.EVA_AUTONOMY_LEVEL || '').trim().toLowerCase();
const autonomyFull = autonomyLevel === 'full' || env.EVA_FULL_AUTONOMY === 'true';

const ASSIST_SATELLITE_ENTITY = /^assist_satellite\.[a-z0-9_]+$/;

/**
 * Parse `alias=assist_satellite.entity_id` entries from a comma-separated env
 * value. Plain entity ids are accepted too. Invalid/non-satellite targets are
 * ignored so a typo can never widen the speaker allowlist.
 */
export function parseVoiceAssistSatelliteTargets(value) {
  const targets = [];
  const seen = new Set();
  for (const item of String(value || '').split(',')) {
    const entry = item.trim();
    if (!entry) continue;
    const separator = entry.indexOf('=');
    const alias = separator >= 0 ? entry.slice(0, separator).trim() : '';
    const entityId = (separator >= 0 ? entry.slice(separator + 1) : entry).trim().toLowerCase();
    if (!ASSIST_SATELLITE_ENTITY.test(entityId) || seen.has(entityId)) continue;
    seen.add(entityId);
    targets.push({
      alias: alias || entityId.slice('assist_satellite.'.length),
      entityId,
    });
  }
  return targets;
}

const voiceAssistSatelliteTargets = parseVoiceAssistSatelliteTargets(
  env.EVA_VOICE_ASSIST_SATELLITES,
);

export const config = {
  root,
  dataDir,

  haUrl: (env.HA_URL || 'http://homeassistant.local:8123').replace(/\/$/, ''),
  haToken: env.HA_TOKEN || '',
  // Lokale Valetudo-interface van Erik. Dit is bewust een aparte integratie:
  // een stofzuiger is geen generieke aan/uit-schakelaar.
  robotUrl: (env.EVA_ROBOT_URL || 'http://10.0.20.24').replace(/\/$/, ''),
  robotEntityId: env.EVA_ROBOT_ENTITY_ID || 'vacuum.valetudo_erik',
  robotMapCameraId: env.EVA_ROBOT_MAP_CAMERA_ID || 'camera.valetudo_erik_map_data',
  robotCameraStream: env.EVA_ROBOT_CAMERA_STREAM || 'tcp://10.0.20.24:6969',

  telegramToken: env.TELEGRAM_BOT_TOKEN || '',
  telegramAllowedIds: (env.TELEGRAM_ALLOWED_IDS || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean)
    .map(Number),

  webPort: Number(env.WEB_PORT || 3210),
  webHost: env.WEB_HOST || '127.0.0.1',
  webToken: env.EVA_WEB_TOKEN || '',
  webSender: env.EVA_WEB_SENDER || 'Jaap',
  householdMembers: (env.EVA_HOUSEHOLD_MEMBERS || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean),
  trustHaPersonMembership: env.EVA_TRUST_HA_PERSON_MEMBERSHIP === 'true',
  oauthPort: 1455,

  model: env.EVA_MODEL || 'gpt-5.6-sol',
  modelVoice: env.EVA_MODEL_VOICE || env.EVA_MODEL || 'gpt-5.6-terra',
  modelResearch: env.EVA_MODEL_RESEARCH || env.EVA_MODEL_VOICE || env.EVA_MODEL || 'gpt-5.6-terra',
  reasoningEffort: env.EVA_REASONING || 'low',
  reasoningChat: env.EVA_REASONING_CHAT || 'none',
  reasoningVoice: env.EVA_REASONING_VOICE || 'low',
  reasoningAutonomy: env.EVA_REASONING_AUTONOMY || 'low',
  reasoningCode: env.EVA_REASONING_CODE || 'high',
  openaiApiKey: env.OPENAI_API_KEY || '',

  // Aparte Python-service voor ArcFace en de eigen encoder.
  brainUrl: (env.EVA_BRAIN_URL || 'http://127.0.0.1:3220').replace(/\/$/, ''),

  tz: env.TZ || 'Europe/Amsterdam',
  autonomousLlmTimeoutMs: Number(env.EVA_AUTONOMOUS_LLM_TIMEOUT_MS || 90_000),
  autonomyLevel: autonomyFull ? 'full' : 'bounded',
  autonomyFull,
  autonomyOpenExploration: autonomyFull || env.EVA_AUTONOMY_OPEN_EXPLORATION === 'true',
  autonomyCodeExecution: autonomyFull || env.EVA_AUTONOMY_CODE_EXECUTION === 'true',
  autonomyIntervalMinutes: Number(env.EVA_AUTONOMY_INTERVAL_MINUTES || (autonomyFull ? 15 : 75)),
  autonomyConversationPauseMinutes: Number(env.EVA_AUTONOMY_CONVERSATION_PAUSE_MINUTES || (autonomyFull ? 2 : 20)),
  // Full autonomy gets a real daily work allocation, not just short attention pings.
  autonomyDailyWorkMinutes: Math.max(
    120,
    Math.min(480, Number(env.EVA_AUTONOMY_DAILY_WORK_MINUTES || 120)),
  ),
  autonomyTurnMinutes: Math.max(
    15,
    Math.min(60, Number(env.EVA_AUTONOMY_TURN_MINUTES || (autonomyFull ? 30 : 15))),
  ),
  sleepStart: env.SLEEP_START || '23:30',
  wakeTime: env.WAKE_TIME || '07:30',
  allowHomeControl: env.ALLOW_HOME_CONTROL !== 'false',
  // Alleen deze Assist-satellieten mogen door `spreek_in_huis` worden gebruikt.
  // Het default is een alias uit de lijst of exact een toegestaan entity_id.
  voiceAssistSatelliteTargets,
  voiceAssistSatelliteDefault: String(env.EVA_VOICE_ASSIST_SATELLITE_DEFAULT || '').trim(),

  workspaceDir: path.join(dataDir, 'workspace'),

  unifiKey: env.UNIFI_API_KEY || '',
  unifiHost: env.UNIFI_HOST || '',
};

process.env.TZ = config.tz;

export default config;
