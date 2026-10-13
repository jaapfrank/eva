import assert from 'node:assert/strict';
import test from 'node:test';
import Database from 'better-sqlite3';

import {
  EXECUTION_LEDGER_INDEX_SQL,
  EXECUTION_LEDGER_TABLE_SQL,
  EXECUTION_LEDGER_TRIGGER_SQL,
} from '../src/executionledger.js';
import {
  initializeTurnUnderstandingAuthorityStore,
  TURN_UNDERSTANDING_AUTHORITY_TABLE,
} from '../src/turnauthority.js';

function exactLedger(database) {
  database.exec(EXECUTION_LEDGER_TABLE_SQL);
  for (const sql of Object.values(EXECUTION_LEDGER_INDEX_SQL)) database.exec(sql);
  for (const sql of Object.values(EXECUTION_LEDGER_TRIGGER_SQL)) database.exec(sql);
}

function exactAuthorityObjectCount(database) {
  return Number(database.prepare(`SELECT count(*) AS value FROM main.sqlite_master
    WHERE name=? OR tbl_name=?`).get(
    TURN_UNDERSTANDING_AUTHORITY_TABLE,
    TURN_UNDERSTANDING_AUTHORITY_TABLE,
  ).value);
}

test('nieuwe authority-store rolt volledig terug bij TEMP namespace-shadow', () => {
  const database = new Database(':memory:');
  try {
    exactLedger(database);
    database.exec(`CREATE TEMP TABLE ${TURN_UNDERSTANDING_AUTHORITY_TABLE}_shadow(value)`);
    assert.throws(
      () => initializeTurnUnderstandingAuthorityStore(database),
      /TEMP shadow/u,
    );
    assert.equal(exactAuthorityObjectCount(database), 0);
  } finally {
    database.close();
  }
});

test('uppercase main namespace-conflict faalt gesloten zonder gedeeltelijke store', () => {
  const database = new Database(':memory:');
  try {
    exactLedger(database);
    database.exec(`CREATE VIEW ${TURN_UNDERSTANDING_AUTHORITY_TABLE.toUpperCase()}_SHADOW
      AS SELECT 1 AS value`);
    assert.throws(
      () => initializeTurnUnderstandingAuthorityStore(database),
      /namespace is inexact/u,
    );
    assert.equal(exactAuthorityObjectCount(database), 0);
  } finally {
    database.close();
  }
});
