import assert from 'node:assert/strict';
import test from 'node:test';

const { currentWeatherForecastFact, currentWeatherForecastFactText } = await import('../src/integrations/weatherdata.js');

test('weerdata resolveert een genoemde plaats en levert morgen uit de providerreeks', async () => {
  const urls = [];
  const fact = await currentWeatherForecastFact('Hoe wordt het weer morgen in Utrecht?', {
    now: () => Date.parse('2026-08-22T10:00:00.000Z'),
    fetchImpl: async (input) => {
      const url = new URL(input); urls.push(url);
      if (url.hostname.startsWith('geocoding-')) return { ok: true, json: async () => ({
        results: [{ name: 'Utrecht', country: 'Nederland', latitude: 52.09, longitude: 5.12 }],
      }) };
      return { ok: true, json: async () => ({ daily: {
        time: ['2026-08-22', '2026-08-23'], weather_code: [3, 61],
        temperature_2m_min: [12, 14], temperature_2m_max: [20, 22],
        precipitation_probability_max: [10, 70], precipitation_sum: [0, 3.4],
      } }) };
    },
  });
  assert.equal(urls.length, 2);
  assert.equal(fact.location, 'Utrecht');
  assert.equal(fact.date, '2026-08-23');
  assert.equal(fact.precipitationProbabilityMax, 70);
  assert.match(currentWeatherForecastFactText(fact), /Utrecht.*70% kans/u);
});

test('weerdata raakt geen provider zonder weer-vraag', async () => {
  assert.equal(await currentWeatherForecastFact('Plan morgen iets leuks in Utrecht.', {
    fetchImpl: async () => { throw new Error('mag niet worden aangeroepen'); },
  }), null);
});
