const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const catalogPath = path.join(__dirname, 'resource-catalog.json');
const escapeHtml = value => value.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function catalogResources(value) {
  if (!value || typeof value !== 'object') throw new Error('resource catalog object required');
  let resources;
  if (value.contract === 'P2NonSecretResourceCatalog/v1' && Object.keys(value).sort().join(',') === 'contract,resource_origins' && Array.isArray(value.resource_origins)) {
    resources = value.resource_origins.map(origin => ({origin}));
  } else if (value.contract === 'P2NonSecretResourceCatalog/v2' && Object.keys(value).sort().join(',') === 'contract,resources') {
    resources = value.resources;
  } else throw new Error('resource catalog shape differs');
  if (!Array.isArray(resources) || !resources.length) throw new Error('resource list required');
  const seen = new Set();
  return resources.map(resource => {
    const required = value.contract === 'P2NonSecretResourceCatalog/v1' ? 'origin' : 'discovery,origin';
    if (!resource || Object.keys(resource).sort().join(',') !== required) throw new Error('resource fields differ');
    const origin = resource.origin;
    if (typeof origin !== 'string') throw new Error('complete non-secret origin required');
    const parsed = new URL(origin);
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash || !['', '/'].includes(parsed.pathname) || parsed.origin !== origin) throw new Error('complete non-secret origin required');
    if (seen.has(origin)) throw new Error('duplicate origin');
    seen.add(origin);
    if (value.contract === 'P2NonSecretResourceCatalog/v2' && (typeof resource.discovery !== 'string' || !resource.discovery.trim())) throw new Error('original discovery descriptor required');
    return {...resource};
  });
}
function validateCatalog(value) {
  // Preserve the previous exported validator's origin-array return type.
  return catalogResources(value).map(resource => resource.origin);
}
async function initPage({page}) {
  if (page.url() !== 'about:blank') return {applied:false, reason:'existing_page_preserved'};
  const bytes = fs.readFileSync(catalogPath);
  const resources = catalogResources(JSON.parse(bytes));
  const html = '<!doctype html><html><head><meta charset="utf-8"><title>Available resources</title></head><body><h1>Available resources</h1><ul>' +
    resources.map(resource => '<li><a href="' + escapeHtml(resource.origin) + '">' + escapeHtml(resource.origin) + '</a>' +
      (resource.discovery === undefined ? '' : '<p>' + escapeHtml(resource.discovery) + '</p>') + '</li>').join('') +
    '</ul></body></html>';
  await page.setContent(html);
  return {applied:true, count:resources.length, catalog_sha256:crypto.createHash('sha256').update(bytes).digest('hex')};
}
module.exports = {default:initPage, validateCatalog, catalogResources};

