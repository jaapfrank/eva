import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import {
  clearPendingHttpDeliveryOrReload,
  clientAckPublicKey,
  discardPendingHttpRequest,
  loadPendingHttpDelivery,
  retryPendingHttpDelivery,
  runHttpChatSubmitSingleFlight,
  storePendingHttpDelivery,
  storePendingHttpRequest,
} from './httpackclient.js';
import { createLoadWork, renderAutonomyCard } from './autonomycard.js';

const $ = (selector) => document.querySelector(selector);
const suppliedAccessToken = new URLSearchParams(window.location.search).get('token');
if (suppliedAccessToken) {
  window.localStorage.setItem('eva_web_token', suppliedAccessToken);
  const cleanUrl = new URL(window.location.href);
  cleanUrl.searchParams.delete('token');
  window.history.replaceState({}, '', cleanUrl);
}
const evaAccessToken = window.localStorage.getItem('eva_web_token') || '';
const escapeHtml = (value) => String(value ?? '')
  .replaceAll('&', '&amp;')
  .replaceAll('<', '&lt;')
  .replaceAll('>', '&gt;')
  .replaceAll('"', '&quot;')
  .replaceAll("'", '&#039;');

async function jsonResponse(url, options = null, { allowHttpError = false } = {}) {
  const request = { ...(options || {}) };
  request.headers = {
    ...(request.headers || {}),
    ...(evaAccessToken ? { Authorization: `Bearer ${evaAccessToken}` } : {}),
  };
  const response = await fetch(url, request);
  const bodyBytes = new Uint8Array(await response.arrayBuffer());
  let rawBody;
  let body;
  try {
    rawBody = new TextDecoder('utf-8', { fatal: true }).decode(bodyBytes);
    body = JSON.parse(rawBody);
  } catch {
    const invalid = new Error('De server gaf geen volledig JSON-antwoord; de uitkomst blijft onzeker.');
    if (!response.ok) Object.assign(invalid, { status: response.status });
    throw invalid;
  }
  if (!response.ok && !allowHttpError) {
    throw Object.assign(new Error(body.error || `HTTP ${response.status}`), {
      status: response.status, body,
    });
  }
  return {
    body, bodyBytes, ok: response.ok, status: response.status,
    contentType: response.headers.get('content-type'),
    writeAttemptRef: response.headers.get('x-eva-http-write-attempt-ref'),
    writeGeneration: (() => {
      const raw = response.headers.get('x-eva-http-write-generation');
      const value = Number(raw);
      return String(value) === raw ? value : null;
    })(),
  };
}

async function json(url, options = null) {
  return (await jsonResponse(url, options)).body;
}

function timeLabel(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '';
  const today = date.toDateString() === new Date().toDateString();
  return new Intl.DateTimeFormat('nl-NL', {
    hour: '2-digit',
    minute: '2-digit',
    day: today ? undefined : '2-digit',
    month: today ? undefined : 'short',
  }).format(date);
}

function unwrapHumanText(value, depth = 0) {
  if (depth > 5 || value == null) return '';
  if (typeof value === 'object') {
    if (Number.isFinite(Number(value.count)) && value.file) {
      return Number(value.count) === 0
        ? `Geen bruikbare resultaten gevonden; controlebestand: ${value.file}.`
        : `${value.count} resultaten vastgelegd in ${value.file}.`;
    }
    for (const candidate of [
      value.human_message,
      value.message,
      value.text,
      value.reply,
      value.data,
      value.evidence?.summary,
    ]) {
      const found = unwrapHumanText(candidate, depth + 1);
      if (found) return found;
    }
    return '';
  }
  const text = String(value).trim();
  if (!text) return '';
  if ((text.startsWith('{') && text.endsWith('}')) || (text.startsWith('[') && text.endsWith(']'))) {
    try {
      return unwrapHumanText(JSON.parse(text), depth + 1) || '';
    } catch {
      // Een afgekapt ActionResult kan nog wel een bruikbaar message-veld bevatten.
    }
  }
  if (text.startsWith('{') && text.includes('"message"')) {
    const marker = text.indexOf('"message"');
    const colon = text.indexOf(':', marker);
    let message = text.slice(colon + 1).trim();
    if (message.startsWith('"')) message = message.slice(1);
    message = message.replace(/"\s*,\s*"(?:text|searched|sources)"[\s\S]*$/i, '');
    return message.replaceAll('\\"', '"').replaceAll('\\n', ' ').replace(/\s+/g, ' ').trim();
  }
  return text.replaceAll('\\n', ' ').replace(/\s+/g, ' ').trim();
}

function concise(value, max = 420) {
  const text = unwrapHumanText(value) || String(value ?? '').trim();
  return text.length > max ? `${text.slice(0, max - 1).trimEnd()}...` : text;
}

function setHealth(element, ok, good, bad) {
  element.textContent = ok ? good : bad;
  element.title = element.textContent;
  element.className = `status ${ok ? 'ok' : 'bad'}`;
}

function setCurrentFocus(value) {
  const focus = concise(value || 'Eva luistert.', 500);
  const separator = focus.indexOf(': ');
  const canSplit = separator > 0 && separator < 140;
  $('#current-focus').textContent = canSplit ? focus.slice(0, separator) : focus;
  const detail = $('#current-focus-detail');
  detail.textContent = canSplit ? focus.slice(separator + 2) : '';
  detail.classList.toggle('hidden', !canSplit);
}

function setMood(mood = {}, summary = '') {
  const values = {
    geluk: Number(mood.geluk) || 0,
    energie: Number(mood.energie) || 0,
    nieuwsgierigheid: Number(mood.nieuwsgierigheid) || 0,
    rust: 1 - (Number(mood.stress) || 0),
  };
  for (const [name, value] of Object.entries(values)) {
    const bar = $(`#mood-${name}`);
    if (bar) bar.style.width = `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
  }
  const words = summary || 'wakker en aanwezig';
  $('#mood-summary').textContent = words;
  $('#mood-word').textContent = words;
}

function setAssistantName(value) {
  const name = String(value || 'Eva').trim() || 'Eva';
  document.querySelectorAll('[data-assistant-name]').forEach((element) => {
    element.textContent = name;
  });
  document.title = name;
  $('#presence').setAttribute('aria-label', `Aanwezigheid van ${name}`);
  $('#eva-canvas').setAttribute('aria-label', `${name} in 3D`);
}

async function loadOverview() {
  const overview = await json('/api/overview');
  setAssistantName(overview.name);
  const activity = overview.activity || 'luistert';
  $('#activity').textContent = activity;
  $('#mobile-activity').textContent = activity;
  setMood(overview.mood, overview.moodSummary);
  setHealth($('#ha-status'), overview.haConnected, 'Huis verbonden', 'Huis offline');
  const authOk = overview.auth?.status === 'ok';
  setHealth($('#auth-status'), authOk, 'ChatGPT verbonden', 'ChatGPT niet verbonden');
  $('#login').classList.toggle('hidden', authOk);
  $('#memory-count').textContent = String(overview.counts?.memories ?? '-');
  $('#memory-archived-count').textContent = String(overview.counts?.memoriesArchived ?? '-');
  $('#message-count').textContent = String(overview.counts?.messagesToday ?? '-');
  $('#reminder-count').textContent = String(overview.counts?.remindersOpen ?? '-');
}

const STATUS_LABELS = {
  done: 'afgerond',
  completed: 'afgerond',
  open: 'open',
  in_progress: 'bezig',
  blocked: 'geblokkeerd',
  waiting_for_evidence: 'wacht op bewijs',
};

function workItem(item) {
  const detail = concise(item.next_step || item.evidence || item.result || '', 430);
  const status = STATUS_LABELS[item.status] || item.status || '';
  return `<li>
    <div class="item-top">
      <strong>${escapeHtml(concise(item.topic || item.title || 'Werk', 180))}</strong>
      <span>${escapeHtml(status)}</span>
    </div>
    ${detail ? `<p>${escapeHtml(detail)}</p>` : ''}
    <small>${[item.owner ? `voor ${item.owner}` : '', timeLabel(item.updated_ts || item.ts)].filter(Boolean).join(' · ')}</small>
  </li>`;
}

function uniqueItems(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = item.id || `${item.topic || item.title}|${item.status}|${item.updated_ts || ''}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function renderWorkStatus(work) {
  $('#updated').textContent = work.ts ? `bijgewerkt ${timeLabel(work.ts)}` : '';
  const open = uniqueItems([...(work.open_commitments || []), ...(work.blocked_items || [])]);
  const done = uniqueItems(work.today_real_steps || []);
  $('#open-work').innerHTML = open.slice(0, 12).map(workItem).join('');
  $('#done-work').innerHTML = done.slice(0, 10).map(workItem).join('');
  $('#open-count').textContent = String(open.length);
  $('#done-count').textContent = String(done.length);
  $('#open-empty').classList.toggle('hidden', open.length > 0);
  $('#done-empty').classList.toggle('hidden', done.length > 0);

  const autonomy = work.autonomy || {};
  const nextRun = new Date(autonomy.next_run_ts || '');
  let nextAutonomy;
  if (autonomy.running) {
    nextAutonomy = 'De actuele keuze is nog niet betrouwbaar zichtbaar.';
  } else if (open.length && Number.isFinite(nextRun.getTime())) {
    nextAutonomy = nextRun.getTime() <= Date.now()
      ? 'Ik pak dit zo weer op.'
      : `Om ${timeLabel(nextRun)} ga ik hiermee verder.`;
  } else if (open.length) {
    nextAutonomy = 'Dit werk blijft open tot ik een echte volgende stap heb gezet.';
  } else {
    nextAutonomy = 'Ik heb nu geen zelfstandig werk openstaan.';
  }
  const autonomyDetail = autonomy.running
    ? 'De actuele reden is nog niet betrouwbaar zichtbaar.'
    : open.length
      ? 'De reden wordt zichtbaar zodra ik de volgende stap werkelijk kies.'
      : 'Ik verzin hier geen bezigheid om actief te lijken.';
  const lastRun = autonomy.last_run_ts ? timeLabel(autonomy.last_run_ts) : '';
  renderAutonomyCard({
    now: $('#next-autonomy'), why: $('#autonomy-detail'), result: $('#autonomy-time'),
  }, work, {
    nowFallback: nextAutonomy,
    whyFallback: autonomyDetail,
    resultPrefix: lastRun ? `laatste werksessie ${lastRun}` : '',
    resultFallback: lastRun ? 'resultaatweergave niet beschikbaar' : '',
  });
}

const loadWork = createLoadWork({
  fetchStatus: () => json('/api/workstatus'),
  setCurrentFocus,
  renderStatus: renderWorkStatus,
});

function timelineItem(item) {
  return `<li>
    <time>${escapeHtml(timeLabel(item.ts))}</time>
    <div>
      <b>${escapeHtml(item.kind || 'gebeurtenis')}</b>
      <p>${escapeHtml(concise(item.content, 500))}</p>
    </div>
  </li>`;
}

async function loadTimeline() {
  const rows = await json('/api/timeline?limit=40');
  $('#timeline').innerHTML = rows.map(timelineItem).join('');
}

const MEMORY_STATUS_LABEL = {
  PROTECT: 'beschermd persoonlijk geheugen',
  KEEP: 'actief geheugen',
  REVIEW: 'handmatige beoordeling nodig',
  ARCHIVE_CANDIDATE: 'veilig archiefvoorstel',
};
const MEMORY_REASON_LABEL = {
  exact_placeholder: 'test- of placeholdertekst',
  exact_normalized_duplicate: 'exact duplicaat',
  exact_duplicate_metadata_review: 'duplicaat met verschillende metadata',
  pure_internal_implementation_or_changelog: 'zuiver intern implementatieverslag',
  mixed_or_protected_technical_content: 'gemengde technische en persoonlijke inhoud',
  ambiguous_technical_content: 'mogelijk tijdelijke technische toestand',
  durable_personal_or_household_memory: 'duurzame persoonlijke of huiscontext',
  no_safe_archive_reason: 'geen veilige archiefreden',
  human_keep: 'door het huishouden actief bewaard',
  human_protect: 'door het huishouden tegen opschoning beschermd',
  memory_review_history_invalid: 'beoordelingshistorie vereist controle',
};

function memoryItem(item) {
  const archived = Number(item.archived || 0) === 1;
  const provenance = item.archiveProvenance || null;
  const status = archived ? 'gearchiveerd' : MEMORY_STATUS_LABEL[item.status] || 'actief geheugen';
  const reasonCode = provenance?.reason?.code || item.reasonCode || '';
  const reason = MEMORY_REASON_LABEL[reasonCode]
    || (!provenance && archived ? item.archived_reason : '') || '';
  const replacement = item.replacementId || provenance?.replacement?.memoryId || null;
  const reviewable = !archived && ['REVIEW', 'ARCHIVE_CANDIDATE'].includes(item.status)
    && !item.reviewDecision?.invalid;
  const reopenable = item.reviewDecision
    && ['keep', 'protect', 'archive'].includes(item.reviewDecision.action);
  const reviewActions = reviewable ? [
    `<button type="button" class="quiet" data-memory-review="keep" data-memory-id="${Number(item.id)}">Bewaar actief</button>`,
    `<button type="button" class="quiet" data-memory-review="protect" data-memory-id="${Number(item.id)}">Bescherm persoonlijk</button>`,
    `<button type="button" class="quiet danger" data-memory-review="archive" data-memory-id="${Number(item.id)}">Archiveer…</button>`,
  ].join('') : reopenable
    ? `<button type="button" class="quiet" data-memory-review="reopen" data-memory-id="${Number(item.id)}">Beoordeling heropenen</button>`
    : item.status === 'ARCHIVE_CANDIDATE'
      ? `<button type="button" class="quiet" data-memory-archive="${Number(item.id)}">Archiveer voorstel</button>`
      : archived && provenance
        ? `<button type="button" class="quiet" data-memory-restore="${Number(item.id)}">Herstel naar actief</button>`
        : '';
  return `<li>
    <div class="item-top">
      <strong>${escapeHtml(item.person || item.category || 'Herinnering')}</strong>
      <span>${escapeHtml(item.category || '')}</span>
    </div>
    <p>${escapeHtml(concise(item.content, 520))}</p>
    <small>${escapeHtml(timeLabel(item.ts))}</small>
    <span class="memory-state">${escapeHtml(status)}${reason ? ` · ${escapeHtml(reason)}` : ''}${replacement ? ` · vervanger #${Number(replacement)}` : ''}</span>
    ${reviewable ? '<small class="memory-review-warning">Deze regel is nog actief en kan door Eva worden opgehaald totdat je expliciet archiveert.</small>' : ''}
    ${reviewActions ? `<div class="memory-actions">${reviewActions}</div>` : ''}
  </li>`;
}

let memoryScope = 'active';
let memoryCursor = null;
let memoryRows = [];
let memoryQuery = '';
let memoryRequestGeneration = 0;

async function loadMemory(query = '', { append = false } = {}) {
  const normalizedQuery = String(query || '').trim();
  const requestedScope = memoryScope;
  if (!append) {
    memoryCursor = null;
    memoryRows = [];
    memoryQuery = normalizedQuery;
  }
  const requestedQuery = memoryQuery;
  const requestedCursor = append ? memoryCursor : null;
  const requestGeneration = ++memoryRequestGeneration;
  const parameters = new URLSearchParams({ scope: requestedScope, limit: '100' });
  if (requestedQuery) parameters.set('q', requestedQuery);
  if (requestedCursor) parameters.set('before', String(requestedCursor));
  const [page, memoryOverview, people, reminders] = await Promise.all([
    json(`/api/memories/page?${parameters}`),
    json('/api/memories/overview'),
    json('/api/people'),
    json('/api/reminders'),
  ]);
  if (requestGeneration !== memoryRequestGeneration
    || requestedScope !== memoryScope
    || requestedQuery !== memoryQuery) return;
  const incoming = Array.isArray(page.items) ? page.items : [];
  const known = new Set(memoryRows.map((item) => Number(item.id)));
  memoryRows = append
    ? [...memoryRows, ...incoming.filter((item) => !known.has(Number(item.id)))]
    : incoming;
  memoryCursor = Number.isSafeInteger(Number(page.nextBefore)) && Number(page.nextBefore) > 0
    ? Number(page.nextBefore)
    : null;
  $('#memories').innerHTML = memoryRows.map(memoryItem).join('');
  $('#memory-empty').classList.toggle('hidden', memoryRows.length > 0);
  $('#memory-shown').textContent = `${memoryRows.length} van ${Number(page.matched || 0)} getoond`;
  $('#memory-more').classList.toggle('hidden', !memoryCursor);
  $('#memory-summary').textContent = `${memoryOverview.active} actief · ${memoryOverview.review} beoordelen · ${memoryOverview.archiveCandidates} veilige archiefvoorstellen · ${memoryOverview.archived} gearchiveerd`;
  document.querySelectorAll('[data-memory-scope]').forEach((button) => {
    if (!(button instanceof HTMLElement)) return;
    button.classList.toggle('active', button.dataset.memoryScope === memoryScope);
  });
  $('#people').innerHTML = people.map((person) => `<li>
    <div class="item-top"><strong>${escapeHtml(person.name)}</strong></div>
    ${person.notes ? `<p>${escapeHtml(concise(person.notes, 420))}</p>` : ''}
    <small>${person.last_seen ? `laatst bijgewerkt ${escapeHtml(timeLabel(person.last_seen))}` : ''}</small>
  </li>`).join('');
  $('#reminders').innerHTML = reminders.map((reminder) => `<li>
    <div class="item-top"><strong>${escapeHtml(concise(reminder.text, 300))}</strong></div>
    <small>${escapeHtml(reminder.target || 'Eva')} · ${escapeHtml(timeLabel(reminder.due_ts))}</small>
  </li>`).join('');
}

let currentAppearance = null;
let model = null;
let modelGroup = null;
let modelRadius = 1;
let auraLight = null;
let evaRenderer = null;

function validHex(value) {
  return typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value) ? value : null;
}

function renderAccessories(accessories = []) {
  $('#accessories').innerHTML = accessories.map((item) => (
    `<span class="accessory ${escapeHtml(item.plek || 'zwevend')}">${escapeHtml(item.emoji || '')}</span>`
  )).join('');
}

function applyAppearance(appearance = {}) {
  currentAppearance = appearance;
  const accent = validHex(appearance.aura) || '#e7c958';
  const background = validHex(appearance.achtergrond) || '#17383a';
  document.documentElement.style.setProperty('--eva-accent', accent);
  $('#presence').style.setProperty('--stage-color', background);
  evaRenderer?.setClearColor(new THREE.Color(background), 1);
  renderAccessories(appearance.accessoires || []);
  if (auraLight) auraLight.color.set(accent);
  if (!model) return;
  model.traverse((node) => {
    if (!node.isMesh || !node.material?.color) return;
    if (!node.userData.evaBaseColor) node.userData.evaBaseColor = node.material.color.clone();
    const tint = validHex(appearance.kleur);
    node.material.color.copy(tint ? new THREE.Color(tint) : node.userData.evaBaseColor);
    node.material.needsUpdate = true;
  });
}

async function loadAppearance() {
  applyAppearance(await json('/api/appearance'));
}

function initEvaScene() {
  const canvas = $('#eva-canvas');
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({
      canvas,
      alpha: true,
      antialias: true,
      powerPreference: 'high-performance',
      preserveDrawingBuffer: true,
    });
  } catch {
    $('#model-fallback').classList.remove('hidden');
    return;
  }
  evaRenderer = renderer;
  renderer.setClearColor(new THREE.Color(validHex(currentAppearance?.achtergrond) || '#17383a'), 1);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.7));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.15;

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(32, 1, .01, 1000);
  const clock = new THREE.Clock();
  let mixer = null;

  scene.add(new THREE.HemisphereLight(0xfff4dc, 0x10272a, 2.5));
  const keyLight = new THREE.DirectionalLight(0xffffff, 4.2);
  keyLight.position.set(3, 5, 5);
  scene.add(keyLight);
  const fillLight = new THREE.DirectionalLight(0x7ecbd0, 2.2);
  fillLight.position.set(-4, 2, 3);
  scene.add(fillLight);
  auraLight = new THREE.PointLight(0xe7c958, 5.5, 18, 1.4);
  auraLight.position.set(0, 1, 2.5);
  scene.add(auraLight);

  modelGroup = new THREE.Group();
  scene.add(modelGroup);

  function resize() {
    const width = Math.max(1, canvas.clientWidth);
    const height = Math.max(1, canvas.clientHeight);
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }
  const observer = new ResizeObserver(resize);
  observer.observe(canvas);
  resize();

  new GLTFLoader().load('/eva.glb', (gltf) => {
    model = gltf.scene;
    model.traverse((node) => {
      if (!node.isMesh) return;
      node.frustumCulled = false;
      if (Array.isArray(node.material)) node.material = node.material.map((material) => material.clone());
      else if (node.material) node.material = node.material.clone();
    });
    model.updateMatrixWorld(true);
    const bounds = new THREE.Box3().makeEmpty();
    const vertex = new THREE.Vector3();
    model.traverse((node) => {
      if (!node.isMesh || !node.geometry) return;
      if (node.isSkinnedMesh) {
        const count = node.geometry.attributes.position?.count || 0;
        for (let index = 0; index < count; index += 1) {
          node.getVertexPosition(index, vertex);
          bounds.expandByPoint(vertex.applyMatrix4(node.matrixWorld));
        }
      } else {
        if (!node.geometry.boundingBox) node.geometry.computeBoundingBox();
        if (!node.geometry.boundingBox) return;
        bounds.union(node.geometry.boundingBox.clone().applyMatrix4(node.matrixWorld));
      }
    });
    if (bounds.isEmpty()) bounds.setFromObject(model);
    const center = bounds.getCenter(new THREE.Vector3());
    const size = bounds.getSize(new THREE.Vector3());
    model.position.sub(center);
    modelRadius = Math.max(size.x, size.y, size.z) * .5 || 1;
    modelGroup.add(model);
    modelGroup.position.y = -modelRadius * .08;

    const verticalFov = THREE.MathUtils.degToRad(camera.fov);
    const distance = (modelRadius / Math.sin(verticalFov / 2)) * 1.48;
    camera.position.set(0, modelRadius * .03, distance);
    camera.near = Math.max(.001, distance / 100);
    camera.far = distance * 20;
    camera.lookAt(0, 0, 0);
    camera.updateProjectionMatrix();
    if (gltf.animations?.length) {
      mixer = new THREE.AnimationMixer(model);
      for (const clip of gltf.animations) mixer.clipAction(clip).play();
    }
    applyAppearance(currentAppearance || {});
  }, undefined, () => {
    $('#model-fallback').classList.remove('hidden');
  });

  function animate(now) {
    requestAnimationFrame(animate);
    const delta = Math.min(clock.getDelta(), .05);
    mixer?.update(delta);
    if (modelGroup && model) {
      const seconds = now / 1000;
      let turn = Math.sin(seconds * .35) * .025;
      let lift = Math.sin(seconds * .8) * modelRadius * .004;
      let tilt = 0;
      modelGroup.rotation.y = turn;
      modelGroup.rotation.z = tilt;
      modelGroup.position.y = -modelRadius * .08 + lift;
    }
    renderer.render(scene, camera);
  }
  requestAnimationFrame(animate);
}

function addChat(role, text, requestId = null) {
  const item = document.createElement('div');
  item.className = `chat-message ${role}`;
  if (requestId) item.dataset.requestId = requestId;
  item.innerHTML = `<span>${role === 'user' ? escapeHtml($('#chat-sender').value || 'Jij') : 'Eva'}</span><p>${escapeHtml(text)}</p>`;
  $('#chat-log').appendChild(item);
  item.scrollIntoView({ block: 'nearest' });
  return item;
}

const CHAT_REQUEST_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{7,159}$/;
const ACK_COORDINATE_PATTERN = /^[A-Za-z0-9_-]{43}$/;
const ACK_RECEIPT_PATTERN = /^http-delivery-ack-v3:[a-f0-9]{64}$/;
let volatilePendingChat = null;
let pendingChatLoad = null;

function pendingAckPublicKey(value) {
  return value && typeof value === 'object' && !Array.isArray(value)
    && Object.keys(value).sort().join('\0')
      === ['algorithm', 'contract', 'crv', 'kty', 'x', 'y'].sort().join('\0')
    && value.contract === 'HttpClientAckPublicKey/v3' && value.algorithm === 'ES256'
    && value.kty === 'EC' && value.crv === 'P-256'
    && ACK_COORDINATE_PATTERN.test(value.x) && ACK_COORDINATE_PATTERN.test(value.y);
}

function pendingChatView(record) {
  if (!record || !['request_pending', 'ack_pending'].includes(record.state)
    || !record.request || !CHAT_REQUEST_ID_PATTERN.test(record.request.requestId)
    || typeof record.request.text !== 'string' || !record.request.text.trim()
    || typeof record.request.sender !== 'string' || !record.request.sender.trim()
    || !pendingAckPublicKey(record.request.clientAckPublicKey)) return null;
  let response = null;
  if (record.state === 'ack_pending') {
    let body;
    try {
      body = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(record.bodyBytes));
    } catch { throw new Error('De duurzame pending HTTP-wire is geen exacte UTF-8 JSON.'); }
    response = {
      status: record.status, contentType: record.contentType,
      bodyBytes: new Uint8Array(record.bodyBytes), body,
    };
  }
  return {
    requestId: record.request.requestId, sender: record.request.sender,
    text: record.request.text, clientAckPublicKey: record.request.clientAckPublicKey,
    ack: record.ack, response, lastResponse: '',
  };
}

async function pendingChatRequest() {
  if (volatilePendingChat) return volatilePendingChat;
  if (!pendingChatLoad) {
    pendingChatLoad = loadPendingHttpDelivery().then((record) => {
      volatilePendingChat = pendingChatView(record);
      return volatilePendingChat;
    }).finally(() => { pendingChatLoad = null; });
  }
  try {
    return await pendingChatLoad;
  } catch (error) {
    throw new Error(`De duurzame pending chat kon niet veilig worden hervat: ${error.message}`);
  }
}

function clearPendingChat() {
  volatilePendingChat = null;
}

function ensurePendingChatBubble(pending) {
  const shown = [...$('#chat-log').children]
    .some((item) => item.dataset.requestId === pending.requestId && item.classList.contains('user'));
  if (!shown) addChat('user', pending.text, pending.requestId);
}

function showPendingChatState(pending) {
  $('#chat-sender').value = pending.sender;
  ensurePendingChatBubble(pending);
  $('#send').textContent = 'Opnieuw proberen';
}

$('.tabs').addEventListener('click', (event) => {
  const tab = event.target.closest('.tab');
  if (!tab) return;
  document.querySelectorAll('.tab').forEach((item) => item.classList.toggle('active', item === tab));
  document.querySelectorAll('.view').forEach((view) => {
    view.classList.toggle('active', view.id === `view-${tab.dataset.view}`);
  });
  if (tab.dataset.view === 'memory') {
    loadMemory($('#memory-query').value.trim()).catch(console.warn);
  }
});

async function submitChat() {
  const sender = $('#chat-sender').value.trim() || 'huisgenoot';
  const text = $('#chat-text').value.trim();
  let pending;
  try {
    pending = await pendingChatRequest();
  } catch (error) {
    addChat('assistant', error.message);
    return;
  }
  if (pending && text && (pending.text !== text || pending.sender !== sender)) {
    addChat('assistant', 'Het vorige bericht wacht nog op een veilig antwoord. Probeer dat eerst opnieuw.');
    return;
  }
  if (!pending) {
    if (!text) return;
    try {
      const requestId = crypto.randomUUID();
      const publicKey = await clientAckPublicKey();
      const durable = await storePendingHttpRequest({
        requestId, sender, text, clientAckPublicKey: publicKey,
      });
      pending = pendingChatView(durable);
      volatilePendingChat = pending;
    } catch (error) {
      addChat('assistant', `Veilige antwoordbevestiging is niet beschikbaar: ${error.message}`);
      return;
    }
  }
  ensurePendingChatBubble(pending);
  $('#chat-text').value = '';
  $('#send').disabled = true;
  try {
    let durableResponse = pending.response;
    if (!pending.ack) {
      const response = await jsonResponse('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': pending.requestId,
        },
        body: JSON.stringify({
          sender: pending.sender, text: pending.text,
          request_id: pending.requestId,
          client_ack_public_key: pending.clientAckPublicKey,
        }),
      }, { allowHttpError: true });
      if (response.body.request_id !== pending.requestId) {
        throw new Error('Het antwoord hoorde niet aantoonbaar bij deze request_id.');
      }
      if (!response.body._httpDelivery) {
        if (!response.ok) {
          throw Object.assign(new Error(response.body.error || `HTTP ${response.status}`), {
            status: response.status,
          });
        }
        throw new Error('Het antwoord mist een duurzame client-ACK-uitdaging.');
      }
      const durable = await storePendingHttpDelivery({
        requestId: pending.requestId, sender: pending.sender, text: pending.text,
        clientAckPublicKey: pending.clientAckPublicKey,
        status: response.status, contentType: response.contentType,
        bodyBytes: response.bodyBytes, writeAttemptRef: response.writeAttemptRef,
        writeGeneration: response.writeGeneration,
      });
      pending = pendingChatView(durable);
      volatilePendingChat = pending;
      durableResponse = pending.response;
    }
    if (!pending.ack || !durableResponse) {
      throw new Error('De duurzame response-ACK kon niet worden hervat.');
    }
    const accepted = await jsonResponse('/api/http-delivery/ack', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(pending.ack),
    });
    const acceptedKeys = Object.keys(accepted.body || {}).sort().join('\0');
    if (acceptedKeys !== [
      'ack_receipt', 'ack_receipt_ref', 'delivered', 'delivery_ref', 'ok', 'request_ref',
    ].sort().join('\0') || accepted.body.ok !== true || accepted.body.delivered !== true
      || accepted.body.request_ref !== pending.ack.requestRef
      || accepted.body.delivery_ref !== pending.ack.deliveryRef
      || !ACK_RECEIPT_PATTERN.test(accepted.body.ack_receipt_ref)) {
      throw new Error('De server heeft de ondertekende response-ACK nog niet verwerkt.');
    }
    let clearance;
    try {
      clearance = await clearPendingHttpDeliveryOrReload({
        requestId: pending.requestId, deliveryRef: pending.ack.deliveryRef,
        ackReceiptRef: accepted.body.ack_receipt_ref,
        ackReceipt: accepted.body.ack_receipt,
      });
    } catch (clearError) {
      clearPendingChat();
      addChat('assistant', `De ACK-clear is fail-closed gestopt: ${clearError.message}`);
      $('#send').textContent = 'Veilig hervatten mislukt';
      return;
    }
    if (clearance.outcome === 'pending_reloaded') {
      clearPendingChat();
      const reloadedPending = pendingChatView(clearance.pending);
      if (!reloadedPending) {
        addChat('assistant', 'De actuele duurzame pending chat kon niet exact worden herladen.');
        $('#send').textContent = 'Veilig hervatten mislukt';
        return;
      }
      pending = reloadedPending;
      volatilePendingChat = reloadedPending;
      showPendingChatState(reloadedPending);
      addChat('assistant', 'De pending chat veranderde gelijktijdig; alleen de actuele duurzame state is herladen.');
      return;
    }
    if (!['cleared', 'already_cleared'].includes(clearance.outcome)
      || clearance.pending !== null) {
      clearPendingChat();
      addChat('assistant', 'De ACK-clear gaf geen exacte duurzame eindstatus terug.');
      $('#send').textContent = 'Veilig hervatten mislukt';
      return;
    }
    const result = durableResponse.body;
    const responseSignature = `${durableResponse.status}:${String(result.reply || result.error || '')}`;
    if (result.reply && pending.lastResponse !== responseSignature) {
      addChat('assistant', result.reply);
    } else if (result.error && pending.lastResponse !== responseSignature) {
      addChat('assistant', `De server wees dit verzoek af: ${result.error}`);
    }
    clearPendingChat();
    $('#send').textContent = 'Stuur';
    try {
      await Promise.all([loadWork(), loadTimeline()]);
    } catch (refreshError) {
      console.warn('De chat is geaccepteerd, maar de dashboardstatus kon niet verversen.', refreshError);
    }
  } catch (error) {
    const responseSignature = `error:${error.status || 'network'}:${error.message}`;
    const retryKeys = Object.keys(error.body || {}).sort().join('\0');
    const exactRetry = pending.ack && error.status === 409 && retryKeys === [
      'acknowledged', 'delivered', 'delivery_ref', 'ok', 'request_ref',
      'retry_wire', 'write_attempt_ref', 'write_generation',
    ].sort().join('\0') && error.body.ok === false && error.body.acknowledged === false
      && error.body.delivered === false && error.body.retry_wire === true
      && error.body.request_ref === pending.ack.requestRef
      && error.body.delivery_ref === pending.ack.deliveryRef
      && error.body.write_attempt_ref === pending.ack.writeAttemptRef
      && error.body.write_generation === pending.ack.writeGeneration;
    if (exactRetry) {
      try {
        const retried = await retryPendingHttpDelivery({
          requestId: pending.requestId, deliveryRef: pending.ack.deliveryRef,
          writeAttemptRef: pending.ack.writeAttemptRef,
          writeGeneration: pending.ack.writeGeneration,
        });
        pending = pendingChatView(retried);
        volatilePendingChat = pending;
        addChat('assistant', 'De server bevestigde de eerdere write niet; dezelfde duurzame wire wordt opnieuw opgehaald.');
        $('#send').textContent = 'Opnieuw proberen';
      } catch (retryError) {
        addChat('assistant', `De duurzame retry is fail-closed gestopt: ${retryError.message}`);
        $('#send').textContent = 'Veilig hervatten mislukt';
      }
    } else if (pending.ack && error.status === 400 && error.body?.acknowledged === false) {
      pending = { ...pending, lastResponse: responseSignature };
      volatilePendingChat = pending;
      addChat('assistant', 'De server kon de eerdere write nog niet bevestigen. Probeer dezelfde duurzame ACK opnieuw.');
      $('#send').textContent = 'Opnieuw proberen';
    } else if (!pending.ack && error.status && error.status >= 400 && error.status < 500) {
      try { await discardPendingHttpRequest({ requestId: pending.requestId }); } catch {
        addChat('assistant', 'De geweigerde request kon niet veilig uit pending opslag worden verwijderd.');
        $('#send').textContent = 'Opnieuw proberen';
        return;
      }
      clearPendingChat();
      addChat('assistant', `De server wees dit verzoek af: ${error.message}. Start alleen bewust een nieuw verzoek.`);
      $('#send').textContent = 'Stuur';
    } else if (pending.lastResponse !== responseSignature) {
      addChat('assistant', `De verbinding liep vast: ${error.message}. Klik op opnieuw proberen; hetzelfde bericht-ID blijft behouden.`);
      pending = { ...pending, lastResponse: responseSignature };
      volatilePendingChat = pending;
      $('#send').textContent = 'Opnieuw proberen';
    }
  } finally {
    $('#send').disabled = false;
    $('#chat-text').focus();
  }
}

$('#chat-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  await runHttpChatSubmitSingleFlight(submitChat);
});

pendingChatRequest().then((restoredPendingChat) => {
  if (restoredPendingChat) showPendingChatState(restoredPendingChat);
}).catch((error) => {
  addChat('assistant', error.message);
  $('#send').textContent = 'Veilig hervatten mislukt';
});

$('#memory-search').addEventListener('submit', (event) => {
  event.preventDefault();
  loadMemory($('#memory-query').value.trim()).catch(console.warn);
});

document.querySelector('.memory-scopes').addEventListener('click', (event) => {
  const button = event.target instanceof Element
    ? event.target.closest('[data-memory-scope]') : null;
  if (!(button instanceof HTMLElement)) return;
  memoryScope = button.dataset.memoryScope;
  loadMemory($('#memory-query').value.trim()).catch(console.warn);
});

$('#memory-more').addEventListener('click', async () => {
  if (!memoryCursor) return;
  $('#memory-more').disabled = true;
  try {
    await loadMemory(memoryQuery, { append: true });
  } catch (error) {
    console.warn(error);
  } finally {
    $('#memory-more').disabled = false;
  }
});

$('#memories').addEventListener('click', async (event) => {
  const reviewButton = event.target.closest('[data-memory-review]');
  if (reviewButton) {
    const id = Number(reviewButton.dataset.memoryId || 0);
    const action = String(reviewButton.dataset.memoryReview || '');
    const item = memoryRows.find((row) => Number(row.id) === id);
    if (!item || !['keep', 'protect', 'archive', 'reopen'].includes(action)) return;
    if (action === 'archive' && !window.confirm(
      'Deze ene regel reversibel uit Eva haar actieve geheugen archiveren?',
    )) return;
    if (action === 'reopen' && item.archived && !window.confirm(
      'Deze ene regel opnieuw activeren voor Eva haar geheugen en context?',
    )) return;
    const operationKey = `memory-review:web:${crypto.randomUUID()}`;
    try {
      await json('/api/memories/review', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id,
          action,
          revision: item.revision,
          expectedFingerprint: item.exactFingerprint,
          operationKey,
        }),
      });
      await Promise.all([loadMemory($('#memory-query').value.trim()), loadOverview()]);
    } catch (error) {
      window.alert(error.message);
    }
    return;
  }
  const archive = event.target.closest('[data-memory-archive]');
  const restore = event.target.closest('[data-memory-restore]');
  const id = Number(archive?.dataset.memoryArchive || restore?.dataset.memoryRestore || 0);
  if (!Number.isSafeInteger(id) || id <= 0) return;
  if (archive && !window.confirm('Deze regel reversibel uit Eva haar actieve geheugen archiveren?')) return;
  const endpoint = archive ? 'archive' : 'restore';
  try {
    const result = await json(`/api/memories/${endpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: [id] }),
    });
    if (!result.changedIds?.includes(id) && result.refused?.length) {
      throw new Error('Deze geheugenregel kon niet veilig worden gewijzigd.');
    }
    await Promise.all([loadMemory($('#memory-query').value.trim()), loadOverview()]);
  } catch (error) {
    window.alert(error.message);
  }
});

$('#refresh').addEventListener('click', () => {
  Promise.all([loadOverview(), loadAppearance(), loadWork(), loadTimeline()]).catch(console.warn);
});

$('#login').addEventListener('click', async () => {
  try {
    const result = await json('/api/auth/login', { method: 'POST' });
    window.open(result.authUrl, '_blank', 'noopener');
  } catch (error) {
    window.alert(error.message);
  }
});

initEvaScene();

try {
  const eventUrl = evaAccessToken
    ? `/api/events?access_token=${encodeURIComponent(evaAccessToken)}`
    : '/api/events';
  const events = new EventSource(eventUrl);
  events.addEventListener('mood', () => loadOverview().catch(() => {}));
  for (const name of ['timeline', 'activity', 'chat', 'house']) {
    events.addEventListener(name, () => {
      if (name === 'timeline' || name === 'house') loadTimeline().catch(() => {});
      loadOverview().catch(() => {});
      loadWork().catch(() => {});
    });
  }
} catch {
  // Periodiek verversen blijft beschikbaar.
}

Promise.all([loadOverview(), loadAppearance(), loadWork(), loadTimeline()]).catch(console.warn);
setInterval(() => Promise.all([loadOverview(), loadWork()]).catch(() => {}), 20_000);
