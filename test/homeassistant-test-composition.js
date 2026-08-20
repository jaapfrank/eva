import { registerHooks } from 'node:module';

const homeAssistantModuleUrl = new URL('../src/integrations/homeassistant.js', import.meta.url).href;
const testComposition = String.raw`
export const __cameraTriggerTest = Object.freeze({
  issue(value) {
    const frames = Object.freeze((Array.isArray(value?.frames) ? value.frames : [])
      .map((frame) => Object.freeze({ ...frame,
        buffer: Buffer.isBuffer(frame?.buffer) ? Buffer.from(frame.buffer) : frame?.buffer })));
    const issued = Object.freeze({ ...value,
      frame: frames[0] || null, frames });
    ISSUED_CAMERA_TRIGGER_EVENTS.add(issued);
    return issued;
  },
  setCachedStates(rows = []) {
    states.clear();
    for (const row of Array.isArray(rows) ? rows : []) {
      if (typeof row?.entity_id === 'string' && row.entity_id) {
        states.set(row.entity_id, Object.freeze({ ...row,
          attributes: Object.freeze({ ...(row.attributes || {}) }) }));
      }
    }
  },
  setCacheConnection(value) {
    connected = value === true;
  },
  captureAndIssue(event, camera, options = undefined) {
    return captureAndIssue({ ...event }, camera, options);
  },
  readBoundedImageResponse(response, maxBytes) {
    return readBoundedImageResponse(response, maxBytes);
  },
});
export const __speechTest = Object.freeze({
  defaultSpeakers,
  playableTtsUrl,
  resolveVoiceAssistSatelliteTarget,
  serviceTimeoutIsAmbiguous,
  voiceAssistSatelliteAnnouncementPlan,
});
`;

registerHooks({
  load(url, context, nextLoad) {
    const loaded = nextLoad(url, context);
    if (url !== homeAssistantModuleUrl || loaded.format !== 'module') return loaded;
    const source = Buffer.isBuffer(loaded.source)
      ? loaded.source.toString('utf8') : String(loaded.source);
    return { ...loaded, source: `${source}\n${testComposition}` };
  },
});
