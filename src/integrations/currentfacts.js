import { currentCryptoMarketFact, currentCryptoMarketFactText } from './marketdata.js';
import { currentWeatherForecastFact, currentWeatherForecastFactText } from './weatherdata.js';

/**
 * Read-only, typed facts that can ground a live answer before slower general
 * research finishes. New domains register one adapter here; callers never
 * need to know a provider's wire format or selection heuristics.
 */
export const CURRENT_FACT_ADAPTERS = Object.freeze([
  Object.freeze({
    id: 'crypto_market',
    read: currentCryptoMarketFact,
    text: currentCryptoMarketFactText,
    data: (fact) => ({ market_fact: fact }),
  }),
  Object.freeze({
    id: 'weather_forecast',
    read: currentWeatherForecastFact,
    text: currentWeatherForecastFactText,
    data: (fact) => ({ weather_fact: fact }),
  }),
]);

/**
 * Return the first applicable current fact in the declared preference order.
 * An unavailable provider is deliberately invisible to the conversation;
 * ordinary research remains available to the caller.
 */
export async function readCurrentFact(question, {
  adapters = CURRENT_FACT_ADAPTERS,
  ...options
} = {}) {
  for (const adapter of adapters) {
    try {
      const fact = await adapter.read(question, options);
      if (!fact) continue;
      const text = String(adapter.text(fact) || '').trim();
      if (!text) continue;
      return Object.freeze({
        adapterId: adapter.id,
        fact,
        text,
        data: Object.freeze(adapter.data(fact)),
      });
    } catch {
      // One failed read-only source must never hide the next applicable one.
    }
  }
  return null;
}
