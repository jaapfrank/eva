import { EventEmitter } from 'node:events';

/**
 * Centrale event-bus.
 * Events:
 *  - 'timeline'  {id, ts, kind, content, meta}   — alles wat er gebeurt (ook dagboek/dromen)
 *  - 'mood'      {mood, note}                    — stemmiing veranderd
 *  - 'activity'  {text}                          — wat Eva nu aan het doen is
 *  - 'chat'      {channel, sender, role, content, ts}
 *  - 'house'     {type, text, data}              — gebeurtenis in huis (via Home Assistant)
 *  - 'auth'      {status}                        — ChatGPT-login status veranderd
 */
export const bus = new EventEmitter();
bus.setMaxListeners(100);

export default bus;
