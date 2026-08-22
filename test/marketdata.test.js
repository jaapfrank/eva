import assert from 'node:assert/strict';
import test from 'node:test';

const { currentCryptoMarketFact, currentCryptoMarketFactText } = await import('../src/integrations/marketdata.js');

test('marktdata resolveert een genoemd crypto-asset via de provider zelf', async () => {
  const urls = [];
  const fact = await currentCryptoMarketFact('Kun je de actuele crypto-koers van Xrp onderzoeken?', {
    now: () => Date.parse('2026-08-22T10:15:00.000Z'),
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

test('marktdata presenteert geen verouderde quote als actuele koers', async () => {
  const fact = await currentCryptoMarketFact('Wat is de actuele koers van XRP?', {
    now: () => Date.parse('2026-08-22T10:40:01.000Z'),
    fetchImpl: async (input) => {
      const url = new URL(input);
      if (url.pathname.endsWith('/search')) return { ok: true, json: async () => ({
        coins: [{ id: 'ripple', name: 'XRP', symbol: 'xrp' }],
      }) };
      return { ok: true, json: async () => ({ ripple: {
        eur: 1.25, last_updated_at: 1_787_393_410,
      } }) };
    },
  });
  assert.equal(fact, null);
});

test('marktdata verzint geen asset wanneer de vraag er geen benoemt', async () => {
  assert.equal(await currentCryptoMarketFact('Kun je de planten water geven?', {
    fetchImpl: async () => { throw new Error('mag niet worden aangeroepen'); },
  }), null);
});

test('marktdata gebruikt geen toevallig gelijknamige token voor een gewone planning', async () => {
  assert.equal(await currentCryptoMarketFact('Ik wil op 2026-08-23 met mijn zus iets buiten doen in Utrecht.', {
    fetchImpl: async () => { throw new Error('mag niet worden aangeroepen'); },
  }), null);
});
