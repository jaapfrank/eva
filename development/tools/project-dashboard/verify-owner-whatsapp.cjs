// Read-only installed UI check. Never pair, send messages or expose a QR value.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.EVA_DASHBOARD_PLAYWRIGHT_PACKAGE);
(async () => {
  const origin = process.env.EVA_DASHBOARD_ORIGIN || 'http://127.0.0.1:44711';
  const browser = await chromium.launch({ executablePath: process.env.EVA_DASHBOARD_CHROMIUM,
    headless: true, args: ['--no-sandbox', '--disable-gpu'] });
  try {
    const page = await browser.newPage(), errors = [], mutations = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      const request = route.request();
      if (!request.url().startsWith(origin + '/')) return route.abort();
      if (!['GET', 'HEAD'].includes(request.method())) { mutations.push(request.method()); return route.abort(); }
      return route.continue();
    });
    await page.goto(origin + '/internal/v1/dashboard#/?section=connections');
    const panel = page.getByRole('region', { name: 'WhatsApp · optioneel', exact: true });
    await panel.getByRole('status').waitFor();
    await panel.getByRole('button', { name: 'WhatsApp verbinden', exact: true }).waitFor();
    assert.equal(await panel.getByLabel('WhatsApp-nummer', { exact: true }).count(), 1);
    const qrVisible = await panel.getByRole('img', { name: 'WhatsApp-koppelcode', exact: true }).count() === 1;
    assert.deepEqual(errors, []); assert.deepEqual(mutations, []);
    if (process.env.EVA_EXPECT_WHATSAPP_QR === '1') assert(qrVisible);
    console.log(JSON.stringify({ status: 'passed', installed_panel: true, qr_visible: qrVisible,
      browser_errors: errors.length, mutating_requests: mutations.length }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
