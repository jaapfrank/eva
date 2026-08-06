import { completeReminder, dueReminders, logTimeline } from './db.js';
import { config } from './config.js';
import { isNight } from './util.js';
import { haConnected, speak, whoIsHome } from './integrations/homeassistant.js';
import { sendRegisteredMessage } from './mind/tools.js';
import { renderReminderText } from './mind/persona.js';

const isSelfReminder = (target) => /^\s*eva\s*$/i.test(String(target || ''));

function nextDue(dueIso, recurrence) {
  const due = new Date(dueIso);
  if (recurrence === 'daily') due.setDate(due.getDate() + 1);
  else if (recurrence === 'weekly') due.setDate(due.getDate() + 7);
  else if (recurrence === 'monthly') due.setMonth(due.getMonth() + 1);
  else return null;
  while (due.getTime() <= Date.now()) {
    if (recurrence === 'daily') due.setDate(due.getDate() + 1);
    else if (recurrence === 'weekly') due.setDate(due.getDate() + 7);
    else due.setMonth(due.getMonth() + 1);
  }
  return due.toISOString();
}

export async function deliverReminder(reminder) {
  if (isSelfReminder(reminder.target)) {
    logTimeline('reminder', `Eigen herinnering: ${reminder.text}`, {
      internalOnly: true,
      reminderId: reminder.id,
    });
    completeReminder(
      reminder.id,
      reminder.recurrence ? nextDue(reminder.due_ts, reminder.recurrence) : null,
    );
    return { ok: true, internal: true };
  }

  if (isNight(new Date(), config.sleepStart, config.wakeTime)) {
    return { ok: false, waiting: true, reason: 'nacht' };
  }

  const text = renderReminderText(reminder.text);
  const broadcast = !String(reminder.target || '').trim();
  const sent = await sendRegisteredMessage(text, broadcast ? 'gezin' : reminder.target, {
    dedupeKey: `reminder:${reminder.id}:${reminder.due_ts}`,
    broadcast,
  });
  if (!sent?.ok) return { ok: false, reason: sent?.info || 'Telegram weigerde het bericht' };

  completeReminder(
    reminder.id,
    reminder.recurrence ? nextDue(reminder.due_ts, reminder.recurrence) : null,
  );
  logTimeline('reminder', `Eva herinnerde ${reminder.target} aan: ${text}`, {
    reminderId: reminder.id,
    familyVisible: true,
    transportAccepted: true,
  });

  if (haConnected()) {
    try {
      const home = await whoIsHome();
      if (home.length) await speak(text);
    } catch {
      // Telegram is al bevestigd; spreken is alleen een extra kanaal.
    }
  }
  return { ok: true, sent };
}

let timer = null;
let running = false;

async function tick() {
  if (running) return;
  running = true;
  try {
    for (const reminder of dueReminders()) {
      try {
        await deliverReminder(reminder);
      } catch (error) {
        logTimeline('system', `Herinnering ${reminder.id} kon nog niet worden bezorgd: ${error?.message || error}`, {
          internalOnly: true,
          reminderId: reminder.id,
        });
      }
    }
  } finally {
    running = false;
  }
}

export function startReminderLoop() {
  if (timer) return;
  setTimeout(tick, 5_000);
  timer = setInterval(tick, 30_000);
}
