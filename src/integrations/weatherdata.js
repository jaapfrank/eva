const PLACE_STOP_WORDS = new Set([
  'Ik', 'Mijn', 'Je', 'Jij', 'Wij', 'We', 'Morgen', 'Vandaag', 'Vanavond',
  'Het', 'De', 'Een', 'Maar', 'En', 'Als', 'Dan', 'Wat', 'Welke', 'Kijk',
]);

function weatherQuestion(question = '') {
  return /\b(?:weer|weersverwachting|regen|neerslag|temperatuur|wind|forecast|weather)\b/iu.test(
    String(question || ''),
  );
}

function placeCandidates(question = '') {
  return [...new Set((String(question || '').normalize('NFKC').match(/\b[A-Z][\p{L}-]{2,48}\b/gu) || [])
    .filter((word) => !PLACE_STOP_WORDS.has(word)))].slice(0, 4);
}

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function localDate(now) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Europe/Amsterdam', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(new Date(now()));
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}`;
}

function tomorrow(now) {
  const current = localDate(now);
  const [year, month, day] = current.split('-').map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + 1));
  return date.toISOString().slice(0, 10);
}

async function providerJson(fetchImpl, url, signal) {
  const response = await fetchImpl(url, { headers: { accept: 'application/json' }, signal });
  if (!response?.ok) throw new Error(`weerbron gaf HTTP ${response?.status || 'onbekend'}`);
  return response.json();
}

function weatherDescription(code) {
  const descriptions = new Map([
    [0, 'helder'], [1, 'overwegend helder'], [2, 'wisselend bewolkt'], [3, 'bewolkt'],
    [45, 'mistig'], [48, 'rijp met mist'], [51, 'lichte motregen'], [53, 'motregen'],
    [55, 'dichte motregen'], [61, 'lichte regen'], [63, 'regen'], [65, 'zware regen'],
    [71, 'lichte sneeuw'], [73, 'sneeuw'], [75, 'zware sneeuw'], [80, 'buien'],
    [81, 'buien'], [82, 'zware buien'], [95, 'onweer'],
  ]);
  return descriptions.get(Number(code)) || 'wisselend weer';
}

export async function currentWeatherForecastFact(question, {
  fetchImpl = globalThis.fetch, timeoutMs = 5_000, now = () => Date.now(),
} = {}) {
  if (typeof fetchImpl !== 'function' || !weatherQuestion(question)) return null;
  const candidates = placeCandidates(question);
  if (!candidates.length) return null;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.max(750, Math.min(12_000, timeoutMs)));
  try {
    for (const candidate of candidates) {
      const geocodeUrl = new URL('https://geocoding-api.open-meteo.com/v1/search');
      geocodeUrl.searchParams.set('name', candidate);
      geocodeUrl.searchParams.set('count', '1');
      geocodeUrl.searchParams.set('language', 'nl');
      geocodeUrl.searchParams.set('format', 'json');
      const place = (await providerJson(fetchImpl, geocodeUrl, controller.signal))?.results?.[0];
      const latitude = finite(place?.latitude), longitude = finite(place?.longitude);
      if (latitude == null || longitude == null || !place?.name) continue;
      const forecastUrl = new URL('https://api.open-meteo.com/v1/forecast');
      forecastUrl.searchParams.set('latitude', String(latitude));
      forecastUrl.searchParams.set('longitude', String(longitude));
      forecastUrl.searchParams.set('daily', 'weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum');
      forecastUrl.searchParams.set('timezone', 'Europe/Amsterdam');
      forecastUrl.searchParams.set('forecast_days', '3');
      const daily = (await providerJson(fetchImpl, forecastUrl, controller.signal))?.daily;
      const date = tomorrow(now);
      const index = Array.isArray(daily?.time) ? daily.time.indexOf(date) : -1;
      const max = finite(daily?.temperature_2m_max?.[index]);
      const min = finite(daily?.temperature_2m_min?.[index]);
      const precipitationProbability = finite(daily?.precipitation_probability_max?.[index]);
      const precipitation = finite(daily?.precipitation_sum?.[index]);
      if (index < 0 || max == null || min == null || precipitationProbability == null) continue;
      return Object.freeze({
        contract: 'CurrentWeatherForecastFact/v1', provider: 'Open-Meteo',
        location: String(place.name), country: String(place.country || ''), date,
        weatherCode: Number(daily.weather_code?.[index]), description: weatherDescription(daily.weather_code?.[index]),
        temperatureMinC: min, temperatureMaxC: max,
        precipitationProbabilityMax: precipitationProbability,
        ...(precipitation == null ? {} : { precipitationSumMm: precipitation }),
        sourceUrls: Object.freeze([geocodeUrl.toString(), forecastUrl.toString()]),
      });
    }
    return null;
  } finally {
    clearTimeout(timer);
  }
}

export function currentWeatherForecastFactText(fact) {
  if (fact?.contract !== 'CurrentWeatherForecastFact/v1') return '';
  const rain = `${Number(fact.precipitationProbabilityMax).toFixed(0)}% kans op neerslag`;
  const amount = Number.isFinite(fact.precipitationSumMm) ? `, ${fact.precipitationSumMm} mm verwacht` : '';
  return `Directe weersverwachting: ${fact.location} op ${fact.date}: ${fact.description}, ${fact.temperatureMinC}–${fact.temperatureMaxC}°C, ${rain}${amount}, via ${fact.provider}.`;
}
