import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';

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

async function json(url, options = null) {
  const request = { ...(options || {}) };
  request.headers = {
    ...(request.headers || {}),
    ...(evaAccessToken ? { Authorization: `Bearer ${evaAccessToken}` } : {}),
  };
  const response = await fetch(url, request);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
  return body;
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

async function loadOverview() {
  const overview = await json('/api/overview');
  const activity = overview.activity || 'luistert';
  $('#activity').textContent = activity;
  $('#mobile-activity').textContent = activity;
  setMood(overview.mood, overview.moodSummary);
  setHealth($('#ha-status'), overview.haConnected, 'Huis verbonden', 'Huis offline');
  const authOk = overview.auth?.status === 'ok';
  setHealth($('#auth-status'), authOk, 'ChatGPT verbonden', 'ChatGPT niet verbonden');
  $('#login').classList.toggle('hidden', authOk);
  $('#memory-count').textContent = String(overview.counts?.memories ?? '-');
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

async function loadWork() {
  const work = await json('/api/workstatus');
  setCurrentFocus(work.current_focus);
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
  if (autonomy.running) {
    $('#next-autonomy').textContent = 'Ik werk hier nu zelfstandig aan.';
  } else if (open.length && Number.isFinite(nextRun.getTime())) {
    $('#next-autonomy').textContent = nextRun.getTime() <= Date.now()
      ? 'Ik pak dit zo weer op.'
      : `Om ${timeLabel(nextRun)} ga ik hiermee verder.`;
  } else if (open.length) {
    $('#next-autonomy').textContent = 'Dit werk blijft open tot ik een echte volgende stap heb gezet.';
  } else {
    $('#next-autonomy').textContent = 'Ik heb nu geen zelfstandig werk openstaan.';
  }
  $('#autonomy-detail').textContent = open[0]?.next_step
    ? concise(open[0].next_step, 420)
    : 'Ik verzin hier geen bezigheid om actief te lijken.';
  const lastRun = autonomy.last_run_ts ? timeLabel(autonomy.last_run_ts) : '';
  const lastOutcome = autonomy.last_result?.productive === false
    && autonomy.last_result?.progressed !== true
    ? 'nog geen nieuw bewezen resultaat'
    : autonomy.last_result?.progressed === true
      ? 'aantoonbaar verder gewerkt'
      : '';
  $('#autonomy-time').textContent = [lastRun ? `laatste werksessie ${lastRun}` : '', lastOutcome]
    .filter(Boolean)
    .join(' · ');
}

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

function memoryItem(item) {
  return `<li>
    <div class="item-top">
      <strong>${escapeHtml(item.person || item.category || 'Herinnering')}</strong>
      <span>${escapeHtml(item.category || '')}</span>
    </div>
    <p>${escapeHtml(concise(item.content, 520))}</p>
    <small>${escapeHtml(timeLabel(item.ts))}</small>
  </li>`;
}

async function loadMemory(query = '') {
  const [memories, people, reminders] = await Promise.all([
    json(`/api/memories${query ? `?q=${encodeURIComponent(query)}` : ''}`),
    json('/api/people'),
    json('/api/reminders'),
  ]);
  $('#memories').innerHTML = memories.map(memoryItem).join('');
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
let activeExpression = null;
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

const EXPRESSION_LABELS = {
  juich: 'Yes!',
  dans: 'Ik dans even.',
  zwaai: 'Hoi!',
  knuffel: 'Kom hier.',
  verdrietig: 'Dit raakt me.',
  schrik: 'O, wacht.',
  hart: 'Lief.',
  knipoog: 'Ik snap je.',
};

function showExpression(expression = {}) {
  const duration = Math.max(800, Math.min(15_000, Number(expression.duurMs) || 4_000));
  activeExpression = { soort: String(expression.soort || ''), until: performance.now() + duration };
  const element = $('#expression');
  element.textContent = expression.tekst || EXPRESSION_LABELS[expression.soort] || '';
  element.classList.toggle('hidden', !element.textContent);
  window.setTimeout(() => {
    if (activeExpression && performance.now() >= activeExpression.until) {
      activeExpression = null;
      element.classList.add('hidden');
    }
  }, duration + 50);
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
      if (activeExpression && now < activeExpression.until) {
        if (activeExpression.soort === 'dans') turn += Math.sin(seconds * 5) * .2;
        if (activeExpression.soort === 'zwaai' || activeExpression.soort === 'knipoog') tilt = Math.sin(seconds * 4) * .04;
        if (activeExpression.soort === 'juich') lift += Math.abs(Math.sin(seconds * 5)) * modelRadius * .06;
        if (activeExpression.soort === 'schrik') lift += modelRadius * .035;
      }
      modelGroup.rotation.y = turn;
      modelGroup.rotation.z = tilt;
      modelGroup.position.y = -modelRadius * .08 + lift;
    }
    renderer.render(scene, camera);
  }
  requestAnimationFrame(animate);
}

function addChat(role, text) {
  const item = document.createElement('div');
  item.className = `chat-message ${role}`;
  item.innerHTML = `<span>${role === 'user' ? escapeHtml($('#chat-sender').value || 'Jij') : 'Eva'}</span><p>${escapeHtml(text)}</p>`;
  $('#chat-log').appendChild(item);
  item.scrollIntoView({ block: 'nearest' });
}

$('.tabs').addEventListener('click', (event) => {
  const tab = event.target.closest('.tab');
  if (!tab) return;
  document.querySelectorAll('.tab').forEach((item) => item.classList.toggle('active', item === tab));
  document.querySelectorAll('.view').forEach((view) => {
    view.classList.toggle('active', view.id === `view-${tab.dataset.view}`);
  });
  if (tab.dataset.view === 'memory') loadMemory().catch(console.warn);
});

$('#chat-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const sender = $('#chat-sender').value.trim() || 'huisgenoot';
  const text = $('#chat-text').value.trim();
  if (!text) return;
  addChat('user', text);
  $('#chat-text').value = '';
  $('#send').disabled = true;
  try {
    const result = await json('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sender, text }),
    });
    addChat('assistant', result.reply);
    await Promise.all([loadWork(), loadTimeline()]);
  } catch (error) {
    addChat('assistant', `De verbinding liep vast: ${error.message}`);
  } finally {
    $('#send').disabled = false;
    $('#chat-text').focus();
  }
});

$('#memory-search').addEventListener('submit', (event) => {
  event.preventDefault();
  loadMemory($('#memory-query').value.trim()).catch(console.warn);
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
  events.addEventListener('appearance', (event) => applyAppearance(JSON.parse(event.data)));
  events.addEventListener('expression', (event) => showExpression(JSON.parse(event.data)));
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
