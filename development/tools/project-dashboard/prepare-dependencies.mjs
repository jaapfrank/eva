// Exact compatibility bridge for query-string's CommonJS consumer of the
// fixed decoder's documented ESM default. The upstream decoder algorithm is unchanged.
import { readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
const root = new URL('./', import.meta.url);
const file = new URL('node_modules/query-string/index.js', root);
const query = JSON.parse(readFileSync(new URL('node_modules/query-string/package.json', root)));
const decoder = JSON.parse(readFileSync(new URL('node_modules/decode-uri-component/package.json', root)));
if (query.version !== '7.1.3' || decoder.version !== '0.5.1' || decoder.type !== 'module') {
  throw new Error('Dashboard decoder compatibility versions changed; review required');
}
const source = readFileSync(file);
const digest = createHash('sha256').update(source).digest('hex');
const before = 'caa3f2c8b45dfe1e91db22ae10743af68de8d96f26515132bb52485ec0f037fa';
const after = 'fc1ca6e1961ba005e554b1bc0de932d6454bf59bfaea03d02c7261dbdcafcdbd';
if (digest === before) {
  const original = "const decodeComponent = require('decode-uri-component');";
  const replacement = "const decodeComponent = require('decode-uri-component').default;";
  const text = source.toString('utf8');
  if (text.split(original).length !== 2) throw new Error('Unexpected query-string import');
  const updated = text.replace(original, replacement);
  if (createHash('sha256').update(updated).digest('hex') !== after) throw new Error('Unexpected compatibility result');
  writeFileSync(file, updated);
} else if (digest !== after) {
  throw new Error('Unexpected dashboard dependency bytes; review required');
}
