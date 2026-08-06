// LLM-laag: praat met ChatGPT via de OAuth-route (chatgpt.com/backend-api/codex/responses,
// zoals OpenClaw) of valt terug op de officiële API met een API-key.
import crypto from 'node:crypto';
import { config } from '../config.js';
import { kvGet, kvSet } from '../db.js';
import { getAccess, authStatus } from './oauth.js';

const CHATGPT_RESPONSES_URL = 'https://chatgpt.com/backend-api/codex/responses';
const API_RESPONSES_URL = 'https://api.openai.com/v1/responses';
const SESSION_ID = crypto.randomUUID();
const RUNTIME_FRESH_MS = 10 * 60_000;
const RUNTIME_KEY = 'llm_runtime_status_v1';
const runtimeState = {
  lastSuccessAt: 0,
  lastFailureAt: 0,
  failureKind: null,
  statusCode: null,
  consecutiveFailures: 0,
  ...(kvGet(RUNTIME_KEY, null) || {}),
};

function persistRuntimeState() {
  try { kvSet(RUNTIME_KEY, runtimeState); } catch { /* diagnose mag modelwerk niet breken */ }
}

function failureKind(error) {
  if (Number(error?.status) === 429) return 'rate_limited';
  if (Number(error?.status) === 401 || Number(error?.status) === 403) return 'authentication';
  if (/abort|timeout|tijdslimiet|timed out/i.test(String(error?.message || ''))) return 'timeout';
  if (Number(error?.status) >= 500) return 'provider_error';
  return 'connection_error';
}

function noteRuntimeSuccess(now = Date.now()) {
  runtimeState.lastSuccessAt = now;
  runtimeState.failureKind = null;
  runtimeState.statusCode = null;
  runtimeState.consecutiveFailures = 0;
  persistRuntimeState();
}

function noteRuntimeFailure(error, now = Date.now()) {
  runtimeState.lastFailureAt = now;
  runtimeState.failureKind = failureKind(error);
  runtimeState.statusCode = Number(error?.status) || null;
  runtimeState.consecutiveFailures += 1;
  persistRuntimeState();
}

export function deriveLlmRuntimeStatus(state = {}, {
  authReady = false,
  now = Date.now(),
  freshMs = RUNTIME_FRESH_MS,
} = {}) {
  if (!authReady) {
    return { status: 'unconfigured', available: false, authenticated: false, detail: 'niet ingelogd en geen API-sleutel' };
  }
  const successAt = Number(state.lastSuccessAt || 0);
  const failureAt = Number(state.lastFailureAt || 0);
  if (failureAt > successAt && now - failureAt <= freshMs) {
    const details = {
      rate_limited: 'ingelogd, maar ChatGPT heeft momenteel geen capaciteit (429)',
      authentication: 'ingelogd, maar de provider weigert de sessie',
      timeout: 'ingelogd, maar de laatste modelaanroep liep vast op tijd',
      provider_error: 'ingelogd, maar de provider gaf bij de laatste aanroep een serverfout',
      connection_error: 'ingelogd, maar de laatste modelverbinding mislukte',
    };
    return {
      status: 'unavailable', available: false, authenticated: true,
      detail: details[state.failureKind] || details.connection_error,
      lastAttemptAt: new Date(failureAt).toISOString(),
      failureKind: state.failureKind || 'connection_error',
      consecutiveFailures: Number(state.consecutiveFailures || 1),
    };
  }
  if (successAt >= failureAt && successAt && now - successAt <= freshMs) {
    return {
      status: 'available', available: true, authenticated: true,
      detail: 'ingelogd en recent succesvol gebruikt',
      lastAttemptAt: new Date(successAt).toISOString(),
    };
  }
  return {
    status: 'unknown', available: null, authenticated: true,
    detail: 'ingelogd; modelcapaciteit is sinds kort niet live bewezen',
    lastAttemptAt: (Math.max(successAt, failureAt) || null)
      ? new Date(Math.max(successAt, failureAt)).toISOString()
      : null,
  };
}

/** Providergezondheid uit echte modelaanroepen; deze functie doet zelf geen
 * kostbare proefaanroep. Inloggen en beschikbare capaciteit zijn aparte zaken. */
export function llmRuntimeStatus(options = {}) {
  const authReady = authStatus().status === 'ok' || !!config.openaiApiKey;
  return deriveLlmRuntimeStatus(runtimeState, { authReady, ...options });
}

/** Maak een user/developer/assistant-bericht in Responses-formaat. */
export function msg(role, text) {
  const type = role === 'assistant' ? 'output_text' : 'input_text';
  return { type: 'message', role, content: [{ type, text }] };
}

/** User-bericht met afbeelding (dataURL) erbij — zo kan Eva door een camera kijken. */
export function imageMsg(text, dataUrl) {
  return {
    type: 'message',
    role: 'user',
    content: [
      { type: 'input_text', text },
      { type: 'input_image', image_url: dataUrl, detail: 'auto' },
    ],
  };
}

function toolDef(t) {
  return { type: 'function', name: t.name, description: t.description, parameters: t.parameters, strict: false };
}

// Items uit eerdere antwoorden mogen geen id's meenemen (stateless hergebruik).
const stripIds = (item) => {
  const rest = { ...item };
  delete rest.id;
  delete rest.status;
  return rest;
};

async function requestOnce(body, { forceRefresh = false, timeoutMs = null } = {}) {
  const useOauth = authStatus().status === 'ok';
  let url, headers;
  if (useOauth) {
    const { accessToken, accountId } = await getAccess({ forceRefresh });
    url = CHATGPT_RESPONSES_URL;
    headers = {
      Authorization: `Bearer ${accessToken}`,
      'chatgpt-account-id': accountId || '',
      'OpenAI-Beta': 'responses=experimental',
      originator: 'codex_cli_rs',
      session_id: SESSION_ID,
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    };
  } else if (config.openaiApiKey) {
    url = API_RESPONSES_URL;
    headers = {
      Authorization: `Bearer ${config.openaiApiKey}`,
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
    };
  } else {
    throw new Error('Geen brein beschikbaar: log in bij ChatGPT (dashboard of `npm run login`) of zet OPENAI_API_KEY in .env');
  }

  const timeout = Number(timeoutMs);
  const signal = Number.isFinite(timeout) && timeout > 0 ? AbortSignal.timeout(timeout) : undefined;
  const res = await fetch(url, { method: 'POST', headers, body: JSON.stringify(body), signal });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    const err = new Error(`LLM-fout ${res.status}: ${text.slice(0, 400)}`);
    err.status = res.status;
    err.viaOauth = useOauth;
    throw err;
  }
  return parseSse(res);
}

/** Lees de SSE-stream en geef het volledige response-object terug. */
async function parseSse(res) {
  const decoder = new TextDecoder();
  let buffer = '';
  let completed = null;
  let failed = null;
  const items = [];

  for await (const chunk of res.body) {
    buffer += decoder.decode(chunk, { stream: true });
    let idx;
    while ((idx = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, idx).trim();
      buffer = buffer.slice(idx + 1);
      if (!line.startsWith('data:')) continue;
      const payload = line.slice(5).trim();
      if (!payload || payload === '[DONE]') continue;
      let data;
      try {
        data = JSON.parse(payload);
      } catch {
        continue;
      }
      if (data.type === 'response.output_item.done' && data.item) items.push(data.item);
      else if (data.type === 'response.completed') completed = data.response;
      else if (data.type === 'response.failed') failed = data.response?.error || data;
    }
  }
  if (failed) {
    const detail = failed.message || JSON.stringify(failed).slice(0, 300);
    const error = new Error(`LLM gaf een fout: ${detail}`);
    const providerStatus = Number(failed.status || failed.status_code || failed.code);
    if (Number.isFinite(providerStatus)) error.status = providerStatus;
    if (!error.status && /\b(?:overloaded|over capacity|temporarily unavailable|service unavailable|try again later)\b/i.test(detail)) {
      error.status = 503;
      error.code = 'EVA_TRANSIENT_PROVIDER_OVERLOAD';
    }
    throw error;
  }
  const output = completed?.output?.length ? completed.output : items;
  if (!output.length) throw new Error('LLM gaf een leeg antwoord (stream zonder output).');
  return { output, usage: completed?.usage || null };
}

/**
 * Eén beurt: instructions + input → {output, text, toolCalls}.
 * Probeert bij een 401 op de OAuth-route één keer een geforceerde token-refresh.
 */
export async function chatComplete({
  instructions, input, tools = [], nativeTools = [], model = config.model,
  effort = config.reasoningEffort, cacheKey = null, timeoutMs = null,
  rateLimitRetries = 3, budgetClass: _budgetClass = null, toolChoice = null,
  request = requestOnce, pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
}) {
  const effectiveCacheKey = cacheKey || 'eva-core';
  const body = {
    model,
    instructions,
    input,
    tools: [...tools.map(toolDef), ...nativeTools],
    tool_choice: tools.length || nativeTools.length ? (toolChoice || 'auto') : 'none',
    parallel_tool_calls: false,
    store: false,
    stream: true,
    // hergebruik van de gecachete persona-prefix (scheelt kosten/latency)
    prompt_cache_key: effectiveCacheKey,
  };

  // Redeneerstand expliciet sturen. Belangrijk: bij gpt-5.5 betekent een
  // ontbrekend reasoning-blok niet "geen thinking"; de server kiest dan zelf en
  // rekent alsnog reasoning-tokens. Daarom sturen we effort:'none' juist wél
  // expliciet. 'auto' laten we weg: dat is geen geldige API-waarde, maar staat
  // voor "server/default" als iemand het toch in de configuratie zet.
  const effortTekst = String(effort ?? '').trim().toLowerCase();
  const reasoningAuto = !effortTekst || ['auto', 'default', 'standaard'].includes(effortTekst);
  const reasoningNone = ['none', 'off', 'false', '0', 'uit', 'disabled'].includes(effortTekst);
  if (!reasoningAuto) {
    body.include = ['reasoning.encrypted_content'];
    body.reasoning = { effort: reasoningNone ? 'none' : effort, summary: 'auto' };
  }

  let result;
  const timeout = Number(timeoutMs);
  const deadline = Number.isFinite(timeout) && timeout > 0 ? Date.now() + timeout : null;
  const remainingTimeout = () => {
    if (!deadline) return timeoutMs;
    const remaining = deadline - Date.now();
    if (remaining <= 0) throw new Error(`LLM totale tijdslimiet van ${timeout}ms bereikt.`);
    return remaining;
  };
  // 429 = account-rate-limit (gedeeld met o.a. Codex). Transiente limieten komen vaak in vlagen: even wachten +
  // opnieuw proberen rijdt er doorheen i.p.v. de hele beurt te laten falen. Backoff 2s/4s/8s, dan pas opgeven.
  const maxRateLimitRetries = Math.max(0, Math.min(3, Number(rateLimitRetries) || 0));
  let providerFallbackUsed = false;
  let transientRetriesUsed = 0;
  const maxTransientRetries = 2;
  for (let poging = 0; ; poging++) {
    try {
      result = await request(body, { timeoutMs: remainingTimeout() });
      noteRuntimeSuccess();
      break;
    } catch (e) {
      if (e.status === 401 && e.viaOauth) {
        try {
          result = await request(body, { forceRefresh: true, timeoutMs: remainingTimeout() });
          noteRuntimeSuccess();
          break;
        } catch (refreshError) {
          noteRuntimeFailure(refreshError);
          throw refreshError;
        }
      }
      if (e.status === 429 && poging < maxRateLimitRetries) {
        const waitMs = 2000 * Math.pow(2, poging);
        if (deadline && Date.now() + waitMs >= deadline) {
          noteRuntimeFailure(e);
          const timeoutError = new Error(`LLM totale tijdslimiet van ${timeout}ms bereikt tijdens wachten op capaciteit.`);
          timeoutError.status = 429;
          throw timeoutError;
        }
        await pause(waitMs);
        continue;
      }
      if (isTransientProviderFailure(e) && transientRetriesUsed < maxTransientRetries) {
        transientRetriesUsed += 1;
        if (!providerFallbackUsed) {
          providerFallbackUsed = true;
          const alternative = alternateModelFor(body.model);
          if (alternative) body.model = alternative;
        }
        // Losse netwerkfouten komen vaak in een heel korte cluster. De tweede
        // herstelpoging krijgt daarom iets meer ademruimte, binnen dezelfde
        // harde beurtdeadline.
        const waitMs = transientRetriesUsed === 1 ? 500 : 1_500;
        if (!deadline || Date.now() + waitMs < deadline) {
          await pause(waitMs);
          continue;
        }
      }
      noteRuntimeFailure(e);
      if (e.status === 429) {
        const limited = new Error('ChatGPT geeft aan dat de limiet bereikt is (429). Eva moet heel even op adem komen.');
        limited.status = 429;
        throw limited;
      }
      throw e;
    }
  }
  const text = responseTextFromOutput(result.output);
  const toolCalls = result.output.filter((o) => o.type === 'function_call');
  return { output: result.output, text, toolCalls, usage: result.usage };
}

function isTransientProviderFailure(error) {
  const status = Number(error?.status || 0);
  return [502, 503, 504].includes(status)
    || /\b(?:overloaded|over capacity|temporarily unavailable|service unavailable|try again later|fetch failed|network|socket|econnreset)\b/i
      .test(String(error?.message || error || ''));
}

function alternateModelFor(model) {
  const current = String(model || '').trim();
  const candidates = [config.modelVoice, config.modelResearch, config.model]
    .map((candidate) => String(candidate || '').trim())
    .filter(Boolean);
  return candidates.find((candidate) => candidate !== current) || null;
}

/** Een Responses-beurt kan meerdere message-items bevatten. Dat zijn
 * alternatieve/opeenvolgende assistentberichten, geen tekstdelen die aan elkaar
 * geplakt horen. Contentblokken binnen het laatste bericht vormen wel één tekst. */
export function responseTextFromOutput(output = []) {
  const messages = (Array.isArray(output) ? output : [])
    .filter((item) => item?.type === 'message')
    .map((item) => (Array.isArray(item.content) ? item.content : [])
      .filter((content) => content?.type === 'output_text')
      .map((content) => String(content.text || ''))
      .join('')
      .trim())
    .filter(Boolean);
  return messages.at(-1) || '';
}

export const __llmTest = {
  responseTextFromOutput,
  isTransientProviderFailure,
  alternateModelFor,
};

function sourceUrlsFrom(value) {
  const found = new Set();
  const seen = new Set();
  const visit = (item) => {
    if (typeof item === 'string') {
      for (const match of item.matchAll(/https?:\/\/[^\s<>"')\]]+/g)) {
        found.add(match[0].replace(/[.,;:!?]+$/, ''));
      }
      return;
    }
    if (!item || typeof item !== 'object' || seen.has(item)) return;
    seen.add(item);
    if (Array.isArray(item)) {
      for (const child of item) visit(child);
      return;
    }
    for (const child of Object.values(item)) visit(child);
  };
  visit(value);
  return [...found].slice(0, 20);
}

/** Actueel internetonderzoek via OpenAI's ingebouwde webzoeker. De gewone
 * functielus roept dit aan als één bewijsbare onderzoekshandeling. */
export async function searchWeb(query, {
  model = config.modelResearch,
  effort = 'medium',
  timeoutMs = config.autonomousLlmTimeoutMs,
  cacheKey = 'eva-web-research',
  budgetClass = null,
} = {}) {
  const vraag = String(query || '').trim();
  if (!vraag) throw new Error('Geen onderzoeksvraag opgegeven.');
  const regulatory = /\b(?:subsidie|wet|regeling|belasting|juridisch|vergunning)\b/i.test(vraag);
  const concise = /\b(?:kort|beknopt|in\s+(?:één|1|twee|2|drie|3)\s+zinnen|alleen\s+de\s+conclusie)\b/i.test(vraag);
  const { text, output, usage } = await chatComplete({
    instructions: `Zoek actueel op internet en geef een compacte Nederlandse onderzoeksnotitie. Vandaag is ${new Date().toISOString().slice(0, 10)}.
Beantwoord precies de gestelde onderzoeksvraag. Gebruik bij voorkeur primaire bronnen en controleer dat iedere genoemde persoon, vraag, prijs of vacature actueel en rechtstreeks bereikbaar is. Een openbare hulpvraag is geen koper en een vraagprijs is geen bewezen verkoopprijs. Bij opdrachten en vacatures mag je “open”, “actueel”, “bereikbaar” of “reageren kan” alleen schrijven wanneer de rechtstreeks geopende bron dat bevestigt én je de exacte toekomstige sluitingsdatum in ISO-vorm noemt. Een geïndexeerde of nog openbare verlopen pagina is geen actuele kans. Scheid gevonden feit, gevolgtrekking en ontbrekend bewijs. Noem conclusie, onzekerheid, peildatum en 2-4 directe bronlinks. Verzin geen bron of feit.${concise ? '\nDe gebruiker vroeg om een kort antwoord: beperk de volledige notitie tot hooguit zes gewone zinnen plus directe bronlinks; maak geen rapport met tussenkoppen.' : ''}${regulatory ? '\nVoor wetten, subsidies of regelingen controleer je zowel de publieks-/uitvoeringspagina als de actuele juridische regeling op overheid.nl of lokaleregelgeving.overheid.nl; meld conflicterende bedragen, data of voorwaarden.' : ''}`,
    input: [msg('user', vraag)],
    nativeTools: [{ type: 'web_search' }],
    model,
    effort,
    cacheKey,
    budgetClass,
    timeoutMs,
  });
  const searchCalls = output.filter((item) => item.type === 'web_search_call').length;
  const searched = searchCalls > 0;
  if (!searched) throw new Error('De webzoeker leverde geen controleerbare zoekactie op.');
  return { text, searched, searchCalls, sources: sourceUrlsFrom([text, output]), usage };
}

// Gereedschappen die diep nadenken verdienen: zodra Eva hieraan begint,
// schakelt haar redeneren omhoog (high) voor de rest van die gedachte.
const CODE_TOOLS = new Set(['voer_uit', 'schrijf_bestand', 'maak_vaardigheid', 'beheer_addon', 'beheer_toolpakket']);

/**
 * Tool-loop: laat het model tools aanroepen tot het een gewoon antwoord geeft.
 * effort kan per ronde omhoog escaleren (naar escalatedEffort) zodra een
 * code-/bouw-tool wordt gebruikt — zo is bouwen altijd op 'high'.
 */
function parseActionResult(raw) {
  try {
    const value = JSON.parse(String(raw || ''));
    return value && typeof value === 'object' ? value : null;
  } catch {
    return /^Fout bij /i.test(String(raw || ''))
      ? { ok: false, verified: false, needs_followup: true, error: String(raw) }
      : null;
  }
}

function failureClassFor(result) {
  if (result?.failure_class) return String(result.failure_class);
  const text = `${result?.error || ''} ${result?.human_message || ''}`.toLowerCase();
  if (/time.?out|tijdslimiet|temporar|tijdelijk|429|rate.?limit/.test(text)) return 'transient';
  if (/permission|bevoegd|toegang|unauthori|forbidden|api.?key|authentic/.test(text)) return 'permission';
  if (/not found|niet gevonden|bestaat niet|404/.test(text)) return 'not_found';
  if (/invalid|ongeldig|validatie|400/.test(text)) return 'invalid_input';
  if (/conflict|bestaat al|already exists|409/.test(text)) return 'conflict';
  if (/unavailable|onbereikbaar|connection refused|econnrefused|niet beschikbaar/.test(text)) return 'unavailable';
  if (/policy|beleid|niet toegestaan|geblokkeerd/.test(text)) return 'policy';
  return 'unknown';
}

function actionNeedsRecovery(result) {
  if (!result) return false;
  if (result.ok === true && /^human_/i.test(String(result.waiting_for || ''))) return false;
  return result.ok === false || result.verified === false
    || result.needs_followup === true || result.partial === true;
}

export async function runAgent({
  instructions, input, tools = [], executeTool, maxRounds = 8, model,
  effort, escalatedEffort = config.reasoningCode, cacheKey = null, onToolCall = null, shouldYield = null,
  finalEffort = 'low', mechanicalEffort = 'low',
  roundTimeoutMs = null, totalTimeoutMs = null, rateLimitRetries = 3, budgetClass = null,
  allowToolOnFinalRound = false, complete = chatComplete,
  stopAfterToolRound = null,
  requireToolCall = false,
  requiredToolsRemaining = null,
  selectTools = null,
  validateFinalText = null,
  finalContextReminder = '',
}) {
  const convo = [...input];
  let huidigEffort = effort;
  let performedToolCalls = 0;
  let recoveryPending = null;
  const tracksRequirements = typeof requiredToolsRemaining === 'function';
  let roundLimit = maxRounds;
  let conclusionRecoveryExtensions = 0;
  let finalReminderAdded = false;
  const total = Number(totalTimeoutMs);
  const deadline = Number.isFinite(total) && total > 0 ? Date.now() + total : null;
  const remaining = () => {
    if (!deadline) return null;
    const ms = deadline - Date.now();
    if (ms <= 0) throw new Error(`Agent totale tijdslimiet van ${total}ms bereikt.`);
    return ms;
  };
  const boundedRoundTimeout = () => {
    const left = remaining();
    const round = Number(roundTimeoutMs);
    if (left == null) return roundTimeoutMs;
    if (!Number.isFinite(round) || round <= 0) return left;
    return Math.min(round, left);
  };
  for (let round = 0; round < roundLimit; round++) {
    // Een gesprek vraagt voorrang. Controleer ook vóór de eerste modelronde en
    // opnieuw vóór iedere daad: tussen modelantwoord en toolcall kan chat starten.
    if (typeof shouldYield === 'function' && shouldYield()) {
      return { text: '', output: [], yielded: true };
    }
    // De laatste modelronde is gereserveerd voor een gewoon antwoord. Voorheen
    // volgde na maxRounds nóg een extra call; een simpele beurt kon daardoor
    // ongemerkt negen modelaanroepen doen. maxRounds is nu werkelijk de harde
    // bovengrens, inclusief de afronding.
    const finalRound = round === roundLimit - 1;
    let roundTools = finalRound && !allowToolOnFinalRound ? [] : tools;
    if (roundTools.length && typeof selectTools === 'function') {
      const selected = selectTools({ round, finalRound, tools: roundTools });
      if (Array.isArray(selected)) roundTools = selected;
    }
    const requiredNow = typeof requiredToolsRemaining === 'function'
      ? requiredToolsRemaining({ round, finalRound })
      : [];
    const requiredRemaining = (Array.isArray(requiredNow) ? requiredNow : [])
      .map((name) => String(name || '').trim()).filter(Boolean);
    // Een eerdere routefout blijft actief totdat het gebruikersdoel aantoonbaar
    // rond is. Een geslaagde diagnose is nog geen geslaagde opdracht.
    if (tracksRequirements && requiredRemaining.length === 0) recoveryPending = null;
    // Een missiestap verklaart zelf de verplichte gereedschapvolgorde. Bied per
    // ronde alleen het eerstvolgende nog ontbrekende gereedschap aan. Een model
    // mag binnen die ronde meerdere gerichte broncalls doen, maar kan daarna
    // niet opnieuw dezelfde bron kiezen ten koste van schrijven en teruglezen.
    // Een verplichte tool beschrijft de voorkeursroute, niet de identiteit van
    // het doel. Na een echte fout blijven alle veilige tools zichtbaar zodat
    // het model kan diagnosticeren, verifiëren of een andere route kiezen.
    if (roundTools.length && requiredRemaining.length && !recoveryPending) {
      const requiredName = requiredRemaining[0];
      const restricted = roundTools.filter((tool) => (
        String(tool?.name || tool?.function?.name || '').trim() === requiredName
      ));
      if (!restricted.length) {
        return {
          text: `De verplichte handeling ${requiredName} kon niet betrouwbaar worden uitgevoerd.`,
          output: [],
          exhausted: true,
          missingRequiredToolCall: true,
          requiredToolUnavailable: requiredName,
        };
      }
      roundTools = restricted;
    }
    const mustCallTool = roundTools.length > 0 && (recoveryPending || (typeof requireToolCall === 'function'
      ? requireToolCall({ round, finalRound }) === true
      : requireToolCall === true));
    if (performedToolCalls > 0 && !mustCallTool && finalContextReminder && !finalReminderAdded) {
      convo.push(msg('user', String(finalContextReminder).slice(0, 1_500)));
      finalReminderAdded = true;
    }
    const mechanicalStep = ['lees_bestand', 'gebruik_capability'].includes(requiredRemaining[0]);
    const roundEffort = mechanicalStep
      ? mechanicalEffort
      : performedToolCalls > 0 && !mustCallTool
        ? finalEffort
        : huidigEffort;
    const { output, text, toolCalls } = await complete({
      instructions,
      input: convo,
      tools: roundTools,
      model,
      effort: roundEffort,
      cacheKey,
      timeoutMs: boundedRoundTimeout(),
      rateLimitRetries,
      budgetClass,
      toolChoice: mustCallTool ? 'required' : null,
    });
    if (!toolCalls.length) {
      if (!mustCallTool) {
        const correction = typeof validateFinalText === 'function'
          ? validateFinalText({ text, round, finalRound, performedToolCalls })
          : '';
        const canExtendConclusionRecovery = finalRound && tools.length
          && conclusionRecoveryExtensions < 1;
        if (correction && (roundTools.length || canExtendConclusionRecovery)) {
          if (canExtendConclusionRecovery) {
            roundLimit += 2;
            conclusionRecoveryExtensions += 1;
          }
          convo.push(...output.map(stripIds));
          convo.push(msg('user', String(correction)));
          recoveryPending = {
            tool: 'eigen conclusiecontrole',
            signature: `conclusion:${round}`,
            failureClass: 'knowledge_gap',
            effectUncertain: false,
            error: String(correction).slice(0, 900),
          };
          continue;
        }
        return { text, output, conclusionValidationFailed: Boolean(correction) };
      }
      if (finalRound) return {
        text: 'De gevraagde handeling is nog niet uitgevoerd. Ik ga niet doen alsof die klaar is.',
        output,
        exhausted: true,
        missingRequiredToolCall: true,
      };
      convo.push(...output.map(stripIds));
      convo.push(msg('user', 'Er staat nog verplicht gereedschap open. Voer nu een van de aangeboden gereedschapshandelingen uit; geef geen tekstantwoord zonder toolcall.'));
      continue;
    }
    if (finalRound && !allowToolOnFinalRound) return {
      text: text || 'Ik heb nog geen betrouwbaar eindresultaat. Ik ga niet doen alsof het gelukt is.',
      output,
      exhausted: true,
    };

    convo.push(...output.map(stripIds));
    let roundFailure = null;
    let roundSuccess = false;
    for (const call of toolCalls) {
      if (typeof shouldYield === 'function' && shouldYield()) {
        return { text: '', output: [], yielded: true };
      }
      if (CODE_TOOLS.has(call.name)) huidigEffort = escalatedEffort; // bouwen → diep nadenken
      performedToolCalls += 1;
      let args = {};
      try {
        args = call.arguments ? JSON.parse(call.arguments) : {};
      } catch {
        args = {};
      }
      let resultText;
      try {
        if (onToolCall) onToolCall(call.name, args);
        const timeLeft = remaining();
        // Reserveer altijd tijd voor de volgende modelronde, zodat een geslaagde
        // tooluitkomst nog in gewone taal kan worden afgerond.
        const toolTimeoutMs = timeLeft == null
          ? null
          : Math.max(1_000, Math.min(120_000, timeLeft - 45_000));
        resultText = String(await executeTool(call.name, args, {
          deadlineTs: deadline,
          timeoutMs: toolTimeoutMs,
        }));
      } catch (e) {
        // Tijdelijke modelcapaciteit binnen een modelgedreven tool is geen
        // inhoudelijk toolresultaat. Laat de missieregie dezelfde stap met haar
        // bestaande checkpoint uitstellen, in plaats van fouttekst als bewijs te boeken.
        if (e?.code === 'EVA_BACKGROUND_LLM_BUDGET'
          || e?.code === 'EVA_TOOL_ATTEMPTS_EXHAUSTED'
          || e?.status === 429) throw e;
        resultText = `Fout bij ${call.name}: ${e.message}`;
      }
      const actionResult = parseActionResult(resultText);
      if (actionNeedsRecovery(actionResult)) {
        roundFailure ||= {
          tool: call.name,
          signature: `${call.name}:${JSON.stringify(args)}`,
          failureClass: failureClassFor(actionResult),
          effectUncertain: actionResult?.effect_uncertain === true,
          error: String(actionResult?.error || actionResult?.human_message || 'onvolledig resultaat').slice(0, 900),
        };
      } else if (actionResult?.ok === true && actionResult?.verified === true) {
        roundSuccess = true;
      }
      // Tool-output wordt in ELKE volgende ronde opnieuw meegestuurd; 8000 chars liet de
      // conversatie in lange tool-lussen opzwellen (groot deel van het tokenverbruik). 3000 is
      // ruim voor vrijwel elke uitkomst (bestand/commando), zónder de lus te laten ballonnen.
      const toolOutputLimit = call.name === 'lees_bestand' ? 12_000 : 3_000;
      convo.push({ type: 'function_call_output', call_id: call.call_id, output: resultText.slice(0, toolOutputLimit) });
    }
    if (roundFailure) recoveryPending = roundFailure;
    else if (!tracksRequirements && roundSuccess) recoveryPending = null;
    if (recoveryPending && !finalRound) {
      const uncertainty = recoveryPending.effectUncertain
        ? ' Het effect kan al zijn opgetreden: lees daarom eerst de actuele toestand terug en herhaal de mutatie niet blind.'
        : '';
      convo.push(msg('user', `Deze aanpak (${recoveryPending.tool}) gaf ${recoveryPending.failureClass}: ${recoveryPending.error}.${uncertainty} Behandel dit als informatie over één route, niet als reden om het doel op te geven. Kies nu zelf een materieel andere veilige aanpak of een gerichte verificatie. Claim pas een blokkade als actuele tooluitvoer precies bewijst welke externe afhankelijkheid ontbreekt.`));
    }
    if (typeof stopAfterToolRound === 'function') {
      const directText = stopAfterToolRound();
      if (directText) {
        return {
          text: typeof directText === 'string' && directText.trim()
            ? directText.trim()
            : 'De gereedschapshandeling is uitgevoerd en gecontroleerd.',
          output,
          toolFinal: true,
        };
      }
    }
    if (finalRound && allowToolOnFinalRound) {
      return {
        text: 'De laatste gereedschapshandeling is uitgevoerd; de uitkomst is nog niet apart gecontroleerd.',
        output,
        toolFinal: true,
      };
    }
  }
  return { text: 'Ik heb nog geen betrouwbaar eindresultaat. Ik ga niet doen alsof het gelukt is.', output: [], exhausted: true };
}
