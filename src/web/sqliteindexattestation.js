import { canonicalJson } from '../util.js';

const safeName = (value) => String(value).replaceAll("'", "''");

function expectedSignature(columnNames, specification) {
  const keyColumns = specification.columns.map((name, seqno) => {
    const cid = columnNames.indexOf(name);
    if (cid < 0) throw new Error(`unknown indexed column ${name}`);
    return {
      seqno, cid, name, desc: 0, coll: 'BINARY', key: 1,
    };
  });
  return {
    name: specification.origin === 'c' ? specification.name : null,
    unique: specification.unique === false ? 0 : 1,
    origin: specification.origin,
    partial: specification.partial === true ? 1 : 0,
    columns: [
      ...keyColumns,
      {
        seqno: keyColumns.length, cid: -1, name: null,
        desc: 0, coll: 'BINARY', key: 0,
      },
    ],
  };
}

/**
 * Attesteert expliciete én SQLite-autoindexes semantisch. Autoindexnamen zijn
 * implementatiedetails; multipliciteit, origin, uniciteit en volledige xinfo
 * (kolom-id, volgorde, collation, DESC en key/auxiliary) zijn wel identiteit.
 */
export function exactSqliteIndexSignatures(database, table, columnNames, specifications) {
  try {
    const actual = database.prepare(`PRAGMA main.index_list('${safeName(table)}')`).all()
      .map((index) => ({
        name: index.origin === 'c' ? index.name : null,
        unique: Number(index.unique), origin: String(index.origin),
        partial: Number(index.partial),
        columns: database.prepare(
          `PRAGMA main.index_xinfo('${safeName(index.name)}')`,
        ).all().map((column) => ({
          seqno: Number(column.seqno), cid: Number(column.cid),
          name: column.name === null ? null : String(column.name),
          desc: Number(column.desc), coll: String(column.coll), key: Number(column.key),
        })),
      }));
    const expected = specifications.map((specification) => (
      expectedSignature(columnNames, specification)
    ));
    const sorted = (items) => items.map((item) => canonicalJson(item)).sort();
    return canonicalJson(sorted(actual)) === canonicalJson(sorted(expected));
  } catch { return false; }
}

