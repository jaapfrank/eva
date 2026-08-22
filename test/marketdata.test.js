import assert from 'node:assert/strict';
import test from 'node:test';

const { currentCryptoMarketFact, currentCryptoMarketFactText } = await import('../src/integrations/marketdata.js');

test('marktdata resolveert een genoemd crypto-asset via de provider zelf', async () => {
  const urls = [];
  const fact = await currentCryptoMarketFact('Kun je Xrp voor mij onderzoeken?', {
    fetchImpl: async (input) => {
      const url = new URL(input); urls.push(url);
      if (url.pathname.endsWith('/search')) return { ok: true, json: async () => ({
        coins: [{ id: 'ripple', name: 'XRP', symbol: 'xrp' }],
      }) };
      if (url.pathname.endsWith('/market_chart')) return { ok: true, json: async () => ({
        prices: [[1_787_000_000_000, 1.1], [1_787_200_000_000, 1.2]],
      }) };
      return { ok: true, json: async () => ({ ripple: {
        eur: 1.25, usd: 1.46, eur_24h_change: 4.857, last_updated_at: 1_787_393_410,
      } }) };
    },
  });
  assert.equal(urls[0].searchParams.get('query'), 'Xrp');
  assert.equal(urls[1].searchParams.get('ids'), 'ripple');
  assert.equal(fact.assetId, 'ripple');
  assert.equal(fact.symbol, 'XRP');
  assert.deepEqual(fact.recentMovement, {
    days: 7, startEur: 1.1, highEur: 1.25, lowEur: 1.1, changePercent: 13.636364,
  });
  assert.match(currentCryptoMarketFactText(fact), /XRP €\s?1,25 \/ US\$\s?1,46/u);
  assert.match(currentCryptoMarketFactText(fact), /In 7 dagen \+13\.64%/u);
});

test('marktdata verzint geen asset wanneer de vraag er geen benoemt', async () => {
  assert.equal(await currentCryptoMarketFact('Kun je de planten water geven?', {
    fetchImpl: async () => { throw new Error('mag niet worden aangeroepen'); },
  }), null);
});
