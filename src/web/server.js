import path from 'node:path';
import { fileURLToPath } from 'node:url';
import express from 'express';
import { config } from '../config.js';
import { runtimeVersion } from '../runtimeversion.js';
import { bus } from '../bus.js';
import db, {
  allMemories,
  allPeople,
  emotionHistory,
  getTimeline,
  openReminders,
  searchMemories,
} from '../db.js';
import { errMsg } from '../util.js';
import { agentStatus, handleMessageDetailed, recordDeliveredReply } from '../mind/agent.js';
import { getMood, moodSummary } from '../mind/emotions.js';
import { authStatus, startLoginFlow } from '../brain/oauth.js';
import { cachedStates, haConnected, speak } from '../integrations/homeassistant.js';
import { getAppearance } from '../mind/appearance.js';
import { autonomyStatus } from '../mind/autonomy.js';
import { listCapabilities } from '../mind/capabilities.js';
import { normalizeVoiceConversationId } from '../mind/voicebridge.js';
import {
  assertSafeWebBinding,
  familyTimelineEntry,
  normalizeWebConversationId,
  requestAuthorized,
} from './access.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const BUS_EVENTS = ['timeline', 'mood', 'activity', 'chat', 'auth', 'house', 'appearance', 'expression'];

export { familyTimelineEntry } from './access.js';

function countsSinceMidnight() {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  return {
    memories: db.prepare('SELECT COUNT(*) AS c FROM memories WHERE COALESCE(archived,0)=0').get().c,
    memoriesArchived: db.prepare('SELECT COUNT(*) AS c FROM memories WHERE COALESCE(archived,0)=1').get().c,
    messagesToday: db.prepare('SELECT COUNT(*) AS c FROM messages WHERE ts >= ?').get(start.toISOString()).c,
    remindersOpen: db.prepare("SELECT COUNT(*) AS c FROM reminders WHERE status = 'open'").get().c,
  };
}

export async function startWeb() {
  assertSafeWebBinding({ host: config.webHost, token: config.webToken });
  const app = express();
  app.disable('x-powered-by');
  app.use(express.json({ limit: '2mb' }));
  app.use('/api', (req, res, next) => {
    if (requestAuthorized(req, config.webToken)) return next();
    return res.status(401).json({ error: 'Geen geldige toegang tot Eva.' });
  });
  app.use('/vendor/three', express.static(path.join(__dirname, '../../node_modules/three')));
  app.use(express.static(path.join(__dirname, 'public')));

  app.get('/api/overview', (_req, res) => {
    try {
      const status = agentStatus();
      res.json({
        name: 'Eva',
        awake: true,
        activity: status.activity,
        mood: getMood(),
        moodSummary: moodSummary(),
        auth: authStatus(),
        haConnected: haConnected(),
        counts: countsSinceMidnight(),
      });
    } catch (error) {
      res.status(500).json({ error: errMsg(error) });
    }
  });

  app.get('/api/health', (_req, res) => {
    try {
      const auth = authStatus();
      const checks = [
        { naam: 'database', ok: true },
        { naam: 'ChatGPT', ok: auth.status === 'ok' || !!config.openaiApiKey, zacht: false },
        {
          naam: 'Home Assistant',
          ok: haConnected(),
          zacht: true,
          cached_states: cachedStates().length,
        },
      ];
      res.json({
        gezond: checks.every((check) => check.ok || check.zacht),
        checks,
        runtimeVersion: runtimeVersion(),
        agent: agentStatus(),
        autonomie: autonomyStatus(),
      });
    } catch (error) {
      res.status(500).json({ error: errMsg(error) });
    }
  });

  app.get('/api/workstatus', (_req, res) => {
    try {
      res.json({ ...agentStatus(), autonomy: autonomyStatus() });
    } catch (error) {
      res.status(500).json({ error: errMsg(error) });
    }
  });

  app.get('/api/capabilities', (_req, res) => {
    try {
      res.json({ capabilities: listCapabilities({ limit: 100 }) });
    } catch (error) {
      res.status(500).json({ error: errMsg(error) });
    }
  });

  app.get('/api/timeline', (req, res) => {
    try {
      const limit = Math.min(Math.max(Number(req.query.limit) || 50, 1), 200);
      const kinds = req.query.kinds
        ? String(req.query.kinds).split(',').map((item) => item.trim()).filter(Boolean)
        : null;
      const before = Number(req.query.before) || null;
      const rows = getTimeline({ limit: 800, kinds, before })
        .filter(familyTimelineEntry)
        .slice(0, limit);
      res.json(rows);
    } catch (error) {
      res.status(500).json({ error: errMsg(error) });
    }
  });

  app.get('/api/memories', (req, res) => {
    try {
      const query = String(req.query.q || '').trim();
      res.json(query ? searchMemories(query, 50) : allMemories(100));
    } catch (error) {
      res.status(500).json({ error: errMsg(error) });
    }
  });

  app.get('/api/people', (_req, res) => {
    try {
      res.json(allPeople());
    } catch (error) {
      res.status(500).json({ error: errMsg(error) });
    }
  });

  app.get('/api/reminders', (_req, res) => {
    try {
      res.json(openReminders());
    } catch (error) {
      res.status(500).json({ error: errMsg(error) });
    }
  });

  app.get('/api/proposals', (_req, res) => res.json([]));

  app.get('/api/emotions/history', (req, res) => {
    try {
      const hours = Math.min(Math.max(Number(req.query.hours) || 24, 1), 24 * 14);
      res.json(emotionHistory(hours));
    } catch (error) {
      res.status(500).json({ error: errMsg(error) });
    }
  });

  app.post('/api/chat', async (req, res) => {
    const sender = config.webSender;
    const text = String(req.body?.text || '').trim();
    if (!text) return res.status(400).json({ error: 'Geen tekst meegegeven.' });
    try {
      const conversationId = normalizeWebConversationId(
        req.body?.conversation_id || req.body?.session_id,
        sender,
      );
      const result = await handleMessageDetailed({
        channel: 'web',
        sender,
        text,
        messageContext: {
          conversationId,
          identityTrusted: true,
          syntheticTest: req.body?.synthetic === true,
        },
      });
      res.json({ reply: result.reply, conversation_id: conversationId, turn_id: result.turnId || null });
      recordDeliveredReply(result, { accepted: true, transport: 'http' });
      return undefined;
    } catch (error) {
      return res.status(500).json({ error: errMsg(error) });
    }
  });

  // Het oude dashboard blijft bruikbaar, maar toont geen verzonnen parallelle
  // "binnenwerelden" meer. Gesprek, geheugen en aantoonbaar werk zijn leidend.
  app.get('/api/innerlife', (_req, res) => {
    res.json({
      drives: {},
      working: agentStatus().current_focus ? [agentStatus().current_focus] : [],
      zelfbeeld: 'Ik ben Eva, een digitale huisgenoot. Wie ik word blijkt uit wat ik onthoud, doe en afmaak.',
      gedachtegang: agentStatus().current_focus || '',
      verwonderingen: [],
      voornemens: [],
      karakter: { afspraken: [] },
      leeftijdDagen: 0,
      autonomie: autonomyStatus(),
      werkstatus: agentStatus(),
      vaardigheden: listCapabilities({ status: 'active', limit: 50 }).map((item) => ({
        soort: item.kind,
        naam: item.name,
        versie: item.version,
        beschrijving: item.description,
        laatstBijgewerkt: item.updated_ts,
      })),
    });
  });

  app.post('/api/agreement/remove', (_req, res) => res.json({ ok: false, afspraken: [] }));

  app.get('/api/appearance', (_req, res) => {
    try {
      res.json(getAppearance());
    } catch (error) {
      res.status(500).json({ error: errMsg(error) });
    }
  });

  app.post('/api/voice', async (req, res) => {
    const sender = config.webSender;
    const text = String(req.body?.text || '').trim();
    if (!text) return res.status(400).json({ error: 'Geen tekst meegegeven.' });
    try {
      const conversationId = normalizeVoiceConversationId(
        req.body?.conversation_id || req.body?.session_id,
        sender,
      );
      const result = await handleMessageDetailed({
        channel: 'voice',
        sender,
        text,
        messageContext: {
          conversationId,
          identityTrusted: true,
          syntheticTest: req.body?.synthetic === true,
        },
      });
      if (req.body?.speak) speak(result.reply, req.body?.speaker || null).catch(() => {});
      res.json({
        reply: result.reply,
        conversation_id: conversationId,
        conversation_active: true,
        continue_listening: true,
        listen_timeout_ms: 30_000,
      });
      recordDeliveredReply(result, { accepted: true, transport: 'http-voice' });
      return undefined;
    } catch (error) {
      return res.status(500).json({ error: errMsg(error) });
    }
  });

  app.post('/api/auth/login', async (_req, res) => {
    try {
      const { authUrl } = await startLoginFlow();
      res.json({ authUrl });
    } catch (error) {
      res.status(500).json({ error: errMsg(error) });
    }
  });

  app.get('/api/events', (req, res) => {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    });
    res.write(': hallo van Eva\n\n');
    const handlers = new Map();
    for (const name of BUS_EVENTS) {
      const handler = (data) => {
        try {
          if (name === 'timeline' && !familyTimelineEntry(data)) return;
          res.write(`event: ${name}\ndata: ${JSON.stringify(data)}\n\n`);
        } catch {
          // De browserverbinding is al gesloten.
        }
      };
      handlers.set(name, handler);
      bus.on(name, handler);
    }
    const ping = setInterval(() => {
      try { res.write(': ping\n\n'); } catch { /* verbinding gesloten */ }
    }, 25_000);
    req.on('close', () => {
      clearInterval(ping);
      for (const [name, handler] of handlers) bus.off(name, handler);
      res.end();
    });
  });

  return new Promise((resolve, reject) => {
    const server = app.listen(config.webPort, config.webHost, () => {
      console.log(`[web] dashboard op http://${config.webHost}:${config.webPort}`);
      resolve(server);
    });
    server.on('error', reject);
  });
}

export default startWeb;
