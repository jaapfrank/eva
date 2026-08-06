// Gedeelde serialisatie voor gezichts-embeddings. De oude, afzonderlijke
// Node/Transformers-modelhost is verwijderd; ArcFace draait via localbrain.
export const vecToBuf = (vec) => (vec ? Buffer.from(vec.buffer.slice(0)) : null);
// Kopieer de bytes naar een VERSE, 0-aligned ArrayBuffer: SQLite/Node-buffers kunnen
// een byteOffset hebben die geen veelvoud van 4 is, en dan zou de Float32Array-
// constructor gooien (RangeError) — wat een leespad stil zou breken. Defensief.
export const bufToVec = (buf) => {
  if (!buf || buf.byteLength < 4) return null;
  try {
    const ab = buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
    return new Float32Array(ab);
  } catch {
    return null;
  }
};
