const STOP_WORDS = new Set([
  'actuele', 'actual', 'prijs', 'price', 'koers', 'quote', 'wat', 'wat', 'zijn', 'is',
  'en', 'the', 'een', 'voor', 'van', 'met', 'over', 'risico', 'risicos', 'belangrijkste',
  'moet', 'ik', 'jij', 'kun', 'kan', 'koop', 'kopen', 'buy', 'should', 'mij', 'mijn',
]);

function candidateSymbols(question = '') {
  const original = String(question || '').normalize('NFKC');
  const words = original.match(/[\p{L}\p{N}]{2,32}/gu) || [];
  const candidates = [];
  for (const word of words) {
    const normalized = word.toLocaleLowerCase('nl-NL');
    if (STOP_WORDS.has(normalized)) continue;
    // Tickers and proper names are the only safe lexical starting points. The
    // provider itself resolves them; Eva never maps a symbol to an asset by a
    // local hard-coded answer table.
    if (/^[A-Z0-9]{2,12}$/u.test(word) || /^[A-Z][\p{L}\p{N}-]{1,31}$/u.test(word)) {
      candidates.push(word);
    }
  }
  return [...new Set(candidates)].slice(0, 4);
}

function finiteQuote(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : null;
}

function rounded(value) {
  return Number(Number(value).toPrecision(8));
}

async function providerJson(fetchImpl, url, signal) {
  const response = await fetchImpl(url, { headers: { accept: 'application/json' }, signal });
  if (!response?.ok) throw new Error(`marktdata gaf HTTP ${response?.status || 'onbekend'}`);
  return response.json();
}

function resolveProviderCoin(candidate, payload) {
  const coins = Array.isArray(payload?.coins) ? payload.coins : [];
  const needle = String(candidate).toLocaleLowerCase('nl-NL');
  return coins.find((coin) => String(coin?.symbol || '').toLocaleLowerCase('nl-NL') === needle)
    || coins.find((coin) => String(coin?.name || '').toLocaleLowerCase('nl-NL') === needle)
    || null;
}

/**
 * Resolves a specifically named crypto asset through the provider, then obtains
 * one timestamped spot quote. It is deliberately an evidence adapter: callers
 * get null when the user did not name an asset or the provider cannot resolve it.
 */
export async function currentCryptoMarketFact(question, {
  fetchImpl = globalThis.fetch,
  timeoutMs = 5_000,
  now = () => Date.now(),
} = {}) {
  if (typeof fetchImpl !== 'function') throw new Error('marktdata-fetch is niet beschikbaar');
  const candidates = candidateSymbols(question);
  if (!candidates.length) return null;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(750, Math.min(12_000, timeoutMs)));
  try {
    for (const candidate of candidates) {
      const searchUrl = new URL('https://api.coingecko.com/api/v3/search');
      searchUrl.searchParams.set('query', candidate);
      const match = resolveProviderCoin(candidate, await providerJson(fetchImpl, searchUrl, controller.signal));
      if (!match?.id) continue;
      const priceUrl = new URL('https://api.coingecko.com/api/v3/simple/price');
      priceUrl.searchParams.set('ids', String(match.id));
      priceUrl.searchParams.set('vs_currencies', 'usd,eur');
      priceUrl.searchParams.set('include_24hr_change', 'true');
      priceUrl.searchParams.set('include_last_updated_at', 'true');
      const quote = (await providerJson(fetchImpl, priceUrl, controller.signal))?.[match.id];
      const usd = finiteQuote(quote?.usd), eur = finiteQuote(quote?.eur);
      if (!usd && !eur) continue;
      const updatedSeconds = Number(quote?.last_updated_at);
      const updatedAt = Number.isFinite(updatedSeconds) && updatedSeconds > 0
        ? new Date(updatedSeconds * 1_000).toISOString() : new Date(now()).toISOString();
      const change = Number(quote?.eur_24h_change);
      return Object.freeze({
        contract: 'CurrentCryptoMarketFact/v1', provider: 'CoinGecko',
        assetId: String(match.id), assetName: String(match.name || match.id),
        symbol: String(match.symbol || candidate).toUpperCase(), updatedAt,
        ...(usd ? { usd: rounded(usd) } : {}),
        ...(eur ? { eur: rounded(eur) } : {}),
        ...(Number.isFinite(change) ? { eur24hChange: rounded(change) } : {}),
        sourceUrls: Object.freeze([searchUrl.toString(), priceUrl.toString()]),
      });
    }
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function currency(value, code) {
  return new Intl.NumberFormat('nl-NL', {
    style: 'currency', currency: code, maximumFractionDigits: value < 10 ? 4 : 2,
  }).format(value);
}

export function currentCryptoMarketFactText(fact) {
  if (!fact || typeof fact !== 'object') return '';
  const prices = [
    Number.isFinite(fact.eur) ? currency(fact.eur, 'EUR') : '',
    Number.isFinite(fact.usd) ? currency(fact.usd, 'USD') : '',
  ].filter(Boolean).join(' / ');
  if (!prices) return '';
  const change = Number.isFinite(fact.eur24hChange)
    ? `; 24 uur ${fact.eur24hChange >= 0 ? '+' : ''}${fact.eur24hChange.toFixed(2)}%`
    : '';
  return `Directe marktquote: ${fact.symbol} ${prices}${change}; bijgewerkt ${fact.updatedAt} via ${fact.provider}.`;
}
