# Eva runtime-contractkaart, automatisch gegenereerd

> Niet handmatig wijzigen. Genereer met `node scripts/generate-runtime-contract-map.mjs` en controleer met `node scripts/generate-runtime-contract-map.mjs --check`.

Bronmoment: `41b73f378b7a15342b3487decafec77f3b580aca9b6b6ff6ef2893c072415b6f`. Bestanden: 76. Statische lokale randen: 326. Dynamische lokale randen: 19. Statische cycli: 0. Runtimecycli inclusief dynamische test- en lazy-loadranden: 1.

Deze laag beschrijft mechanisch alle JavaScriptmodules onder `src` plus lokale modules die zij importeren. Imports, exports en directe moduleconsumers (`importedBy`) komen uit de AST. Dit zijn geen functiecallsites. SQL-, bus-, route-, environment- en effectregels zijn statische detecties; een lege detectie betekent niet dat runtimegedrag onmogelijk is. Semantische runtimecontracten en foutpaden staan in `docs/eva-runtime-contracts.md`.

## Domeinoverzicht

| Domein | Bestanden | Regels | Lokale imports | Exports |
| --- | ---: | ---: | ---: | ---: |
| brain | 2 | 1643 | 6 | 10 |
| browser | 3 | 2005 | 2 | 13 |
| deployment | 1 | 579 | 0 | 35 |
| integrations | 4 | 6331 | 20 | 49 |
| mind | 45 | 57500 | 214 | 510 |
| runtime | 10 | 8095 | 34 | 186 |
| secrets | 2 | 134 | 4 | 7 |
| security | 1 | 47 | 1 | 3 |
| web | 8 | 6619 | 45 | 51 |

## Entry- en bladmodules

Entry-achtig, zonder directe moduleconsumer: `src/index.js`, `src/mind/artifactcollections.js`, `src/mind/materiallifecycle.js`, `src/mind/voiceingress.js`, `src/web/public/app.js`.

Bladmodules, zonder lokale import: `deploy/eva-runtime-repair-protocol.mjs`, `src/bus.js`, `src/config.js`, `src/executionledger.js`, `src/mind/embeddings.js`, `src/mind/monitoringcontract.js`, `src/mind/turnplan.js`, `src/util.js`, `src/web/access.js`, `src/web/public/autonomycard.js`, `src/web/public/httpackclient.js`, `src/web/requestidentity.js`.

## Runtimecycli inclusief dynamische imports

Deze SCCs zijn geen bewijs dat ESM-initialisatie cyclisch is. Ze tonen callback-, lazy-load- en testcompositieranden die samen terug kunnen lopen.

- `src/brain/llm.js` -> `src/db.js` -> `src/integrations/agentmail.js` -> `src/integrations/homeassistant.js` -> `src/integrations/robot.js` -> `src/integrations/telegram.js` -> `src/mind/agent.js` -> `src/mind/artifacts.js` -> `src/mind/capabilities.js` -> `src/mind/cognitive.js` -> `src/mind/communication.js` -> `src/mind/curiosity.js` -> `src/mind/economichandoff.js` -> `src/mind/emotions.js` -> `src/mind/facerec.js` -> `src/mind/home.js` -> `src/mind/household.js` -> `src/mind/learning.js` -> `src/mind/localbrain.js` -> `src/mind/materialcompletion.js` -> `src/mind/perceptionattention.js` -> `src/mind/portfolio.js` -> `src/mind/social.js` -> `src/mind/telegramdelivery.js` -> `src/mind/toolinstaller.js` -> `src/mind/tools.js` -> `src/mind/turnunderstandingoutcome.js` -> `src/mind/understanding.js` -> `src/mind/vision.js` -> `src/mind/voicebridge.js` -> `src/mind/work.js` -> `src/mind/workcells.js` -> `src/self/vault.js` -> `src/voiceauthority.js` -> `src/web/httpingress.js`

## Bestandcontracten

### `deploy/eva-runtime-repair-protocol.mjs`

- Omvang: 579 regels, 29120 bytes. SHA-256 `f9065eb6af9f882ff1cf7d268080051c67e1db8c0b4df625a511fe1d734e0951`.
- Invoer uit lokale modules: geen.
- Externe invoer/dependencies: `node:fs`, `node:path`, `node:child_process`, `node:crypto`.
- Uitvoer/API: `PROTOCOL` (value, regel 8), `MAX_PATCH_BYTES` (value, regel 9), `MAX_CHANGED_LINES` (value, regel 10), `MAX_FILES` (value, regel 11), `ID_PATTERN` (value, regel 12), `HASH_PATTERN` (value, regel 13), `ROOT_GATE_NAMES` (value, regel 14), `RELEASE_PATHS` (value, regel 17), `PROTECTED_SOURCE` (value, regel 26), `PHASE_MATRIX` (value, regel 40), `sha256` (value, regel 64), `fsyncDirectory` (function, regel 66), `atomicBlob` (function, regel 71), `atomicJson` (value, regel 85), `writeNewFile` (function, regel 88), `securePath` (function, regel 112), `readRegular` (function, regel 118), `canonicalRelativePath` (function, regel 138), `repairSourcePath` (function, regel 144), `contained` (function, regel 151), `createGitTrackedFiles` (function, regel 175), `gitTrackedFiles` (value, regel 186), `fileSetSha256` (function, regel 197), `runtimeTreeSha256` (function, regel 218), `dependencyTreeSha256` (function, regel 249), `trackedManifest` (function, regel 266), `attestRelease` (function, regel 287), `dateMillis` (function, regel 307), `parseUnifiedDiff` (function, regel 390), `applyHunks` (function, regel 402), `phaseContract` (function, regel 425), `activeRuntimeFor` (function, regel 431), `validateRootReceipt` (function, regel 462), `transactionResult` (function, regel 502), `validateRootResult` (function, regel 547).
- Directe consumers: `src/mind/runtimecontroller.js`, `src/runtimeversion.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `filesystem`, `process-spawn`.

### `src/brain/llm.js`

- Omvang: 1443 regels, 62781 bytes. SHA-256 `9390dbc97d282a406a8d1a631c3b29f7aadfe7628419a1ed785ad4dcccc65ed6`.
- Invoer uit lokale modules: `src/config.js`, `src/db.js`, `src/brain/oauth.js`, `src/util.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `llmRuntimeStatus` (function, regel 48), `msg` (function, regel 91), `imageMsg` (function, regel 97), `chatComplete` (function, regel 580), `searchWeb` (function, regel 786), `executionModelFailureText` (function, regel 1077), `runExecutionDriver` (function, regel 1428).
- Directe consumers: `src/integrations/telegram.js`, `src/mind/agent.js`, `src/mind/perceptiondecision.js`, `src/mind/tools.js`, `src/mind/vision.js`, `src/mind/workcells.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `timer`.

### `src/brain/oauth.js`

- Omvang: 200 regels, 7670 bytes. SHA-256 `245c09674ab63d7f53ad1c0b4a8b561776514a330d1a9d8349d4342214c58ef4`.
- Invoer uit lokale modules: `src/config.js`, `src/bus.js`.
- Externe invoer/dependencies: `node:http`, `node:crypto`, `node:fs`, `node:path`.
- Uitvoer/API: `authStatus` (function, regel 54), `getAccess` (function, regel 92), `startLoginFlow` (function, regel 113).
- Directe consumers: `src/brain/llm.js`, `src/index.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert `auth`.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `filesystem`, `network`, `timer`.

### `src/bus.js`

- Omvang: 17 regels, 659 bytes. SHA-256 `35a88a80479f72c844a9ee5586dade0b4dee9eac953e62b70a01393bedfee4fa`.
- Invoer uit lokale modules: geen.
- Externe invoer/dependencies: `node:events`.
- Uitvoer/API: `bus` (value, regel 13), `default` (default, regel 16).
- Directe consumers: `src/brain/oauth.js`, `src/db.js`, `src/integrations/homeassistant.js`, `src/mind/agent.js`, `src/mind/autonomy.js`, `src/mind/cognitive.js`, `src/mind/curiosity.js`, `src/mind/emotions.js`, `src/mind/perception.js`, `src/mind/work.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: geen marker.

### `src/config.js`

- Omvang: 556 regels, 23142 bytes. SHA-256 `23b46c2c87001b6b3b8bf36b0dd0790458c0eaaaf621991bffce6d8c47573386`.
- Invoer uit lokale modules: geen.
- Externe invoer/dependencies: `dotenv/config`, `node:fs`, `node:path`, `node:url`.
- Uitvoer/API: `nodeTestContextPresent` (function, regel 36), `resolveEvaDataDir` (function, regel 57), `assertTestDataDirRealPath` (function, regel 177), `requireConfiguredDataRoot` (function, regel 208), `databaseFileMetadataProblem` (function, regel 255), `attestConfiguredDatabaseOpen` (function, regel 366), `attestConfiguredDatabaseSidecars` (function, regel 385), `configuredDatabaseFile` (function, regel 404), `parseVoiceAssistSatelliteTargets` (function, regel 429), `config` (value, regel 459), `default` (default, regel 555).
- Directe consumers: `src/brain/llm.js`, `src/brain/oauth.js`, `src/db.js`, `src/index.js`, `src/integrations/homeassistant.js`, `src/integrations/robot.js`, `src/integrations/telegram.js`, `src/mind/agent.js`, `src/mind/artifactcollections.js`, `src/mind/autonomy.js`, `src/mind/capabilities.js`, `src/mind/curiosity.js`, `src/mind/localbrain.js`, `src/mind/materiallifecycle.js`, `src/mind/perception.js`, `src/mind/perceptionattention.js`, `src/mind/perceptiondecision.js`, `src/mind/persona.js`, `src/mind/projectexecution.js`, `src/mind/runtimecontroller.js`, `src/mind/toolinstaller.js`, `src/mind/tools.js`, `src/mind/vision.js`, `src/mind/workspacefilemanifest.js`, `src/mind/workspacegeneration.js`, `src/reminders.js`, `src/runtimeversion.js`, `src/security/redaction.js`, `src/self/secretmind.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: `TZ`.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `environment`, `filesystem`.

### `src/db.js`

- Omvang: 4568 regels, 198350 bytes. SHA-256 `16abb360f50c8d1c54e2f9529381d008101b57b06395a7045a4c1f1a5c362a00`.
- Invoer uit lokale modules: `src/config.js`, `src/bus.js`, `src/util.js`, `src/security/redaction.js`, `src/executionledger.js`, `src/turnauthority.js`, `src/voiceauthority.js`, `src/executionledger.js`.
- Externe invoer/dependencies: `node:fs`, `node:path`, `node:crypto`, `better-sqlite3`.
- Uitvoer/API: `EXECUTION_RECORD_CONTRACT_V1` (re-export, regel 42), `EXECUTION_RECORD_CONTRACT_V2` (re-export, regel 43), `EXECUTION_VERIFICATION_LEGACY_UNVERIFIABLE` (re-export, regel 44), `EXECUTION_VERIFICATION_VERIFIED` (re-export, regel 45), `db` (value, regel 75), `executionLedgerStoreStatus` (function, regel 533), `turnUnderstandingAuthorityStoreStatus` (function, regel 537), `voiceDeliveryAuthorityStoreStatus` (function, regel 541), `persistTimelineWithoutPublication` (function, regel 612), `publishPersistedTimeline` (function, regel 623), `logTimeline` (function, regel 630), `consumeContributionMutationAuthority` (function, regel 640), `logContributionOutcome` (function, regel 688), `getTimeline` (function, regel 752), `TELEGRAM_DELIVERY_STATUS` (value, regel 773), `TELEGRAM_TRANSPORT_PROTOCOL` (value, regel 786), `TELEGRAM_RECEIPT_PROTOCOL` (value, regel 787), `TELEGRAM_TRANSPORT_MAX_ATTEMPTS` (value, regel 788), `canonicalTelegramText` (function, regel 790), `canonicalTelegramCaption` (function, regel 796), `normalizeTelegramRecipient` (function, regel 806), `resolveTelegramTargets` (function, regel 811), `validateTelegramAcceptedReceipt` (function, regel 917), `validateTelegramAcceptedReplyRecoveryEvidence` (function, regel 1045), `validateTelegramDeliveryProof` (function, regel 1245), `validateExecutionRecord` (function, regel 1473), `recentVerifiedTurnToolReceipts` (function, regel 1483), `claimTurnToolExecution` (function, regel 1690), `transitionTurnToolExecution` (function, regel 1731), `completeTurnToolExecution` (function, regel 1781), `recordTurnToolObservation` (function, regel 1833), `deriveVoiceRetryIntent` (function, regel 1897), `persistMessageWithoutPublication` (function, regel 1962), `publishPersistedMessage` (function, regel 1982), `addMessage` (function, regel 1989), `recentMessages` (function, regel 2009), `recentConversation` (function, regel 2018), `recentOtherConversationsForPerson` (function, regel 2034), `relevantOlderConversationForPerson` (function, regel 2132), `archiveTelegramMessage` (function, regel 2286), `telegramMessage` (function, regel 2359), `newerTelegramUserMessage` (function, regel 2367), `telegramOutboundByDedupeKey` (function, regel 2394), `pendingPreparedTelegramMessages` (function, regel 2414), `hasDeliveredTurn` (function, regel 2444), `markTelegramMessageDeleted` (function, regel 2459), `claimTelegramMessage` (function, regel 2466), `completeTelegramMessage` (function, regel 2502), `failTelegramMessage` (function, regel 2515), `markTelegramMessageDeliveryState` (function, regel 2527), `recoverInterruptedTelegramMessages` (function, regel 2555), `recentTelegramMessages` (function, regel 2562), `searchTelegramMessages` (function, regel 2611), `telegramConversationSearchSpec` (function, regel 2685), `searchTelegramConversationOpportunity` (function, regel 2750), `prepareTelegramConversationOpportunity` (function, regel 2873), `isSensitiveMemoryContent` (function, regel 3017), `lowUtilityMemoryReason` (function, regel 3032), `MEMORY_HYGIENE_STATUS` (value, regel 3056), `MEMORY_HYGIENE_PREVIEW_CONTRACT` (value, regel 3062), `MEMORY_HYGIENE_ARCHIVE_CONTRACT` (value, regel 3063), `MEMORY_PAGE_CONTRACT` (value, regel 3064), `MEMORY_REVIEW_MUTATION_CONTRACT` (value, regel 3065), `MEMORY_REVIEW_ARCHIVE_CONTRACT` (value, regel 3066), `normalizeMemoryExactText` (function, regel 3095), `memoryReviewRevision` (function, regel 3131), `previewMemoryHygieneRows` (function, regel 3222), `memoryHygienePreview` (function, regel 3486), `listArchivedMemories` (function, regel 3636), `memoryPage` (function, regel 3668), `reviewMemoryAction` (function, regel 3985), `archiveMemoryHygieneCandidates` (function, regel 4020), `restoreMemoryHygieneArchive` (function, regel 4072), `addMemory` (function, regel 4118), `latestDailyBackup` (function, regel 4185), `dailyBackupDue` (function, regel 4202), `backupData` (function, regel 4207), `bumpRecall` (function, regel 4267), `searchMemories` (function, regel 4276), `allMemories` (function, regel 4321), `memoriesForPerson` (function, regel 4381), `logEmotion` (function, regel 4423), `emotionHistory` (function, regel 4428), `addReminder` (function, regel 4438), `dueReminders` (function, regel 4457), `openReminders` (function, regel 4461), `completeReminder` (function, regel 4465), `cancelReminder` (function, regel 4470), `upsertPerson` (function, regel 4476), `personByTelegram` (function, regel 4489), `allPeople` (function, regel 4493), `addAspiration` (function, regel 4501), `activeAspirations` (function, regel 4532), `touchAspiration` (function, regel 4536), `kvGet` (function, regel 4543), `kvSet` (function, regel 4548), `maintain` (function, regel 4556), `default` (default, regel 4567).
- Directe consumers: `src/brain/llm.js`, `src/index.js`, `src/integrations/agentmail.js`, `src/integrations/homeassistant.js`, `src/integrations/telegram.js`, `src/mind/agent.js`, `src/mind/appearance.js`, `src/mind/artifactcollections.js`, `src/mind/artifacts.js`, `src/mind/autonomy.js`, `src/mind/capabilities.js`, `src/mind/cognitive.js`, `src/mind/communication.js`, `src/mind/curiosity.js`, `src/mind/economichandoff.js`, `src/mind/emotions.js`, `src/mind/facerec.js`, `src/mind/home.js`, `src/mind/household.js`, `src/mind/learning.js`, `src/mind/localbrain.js`, `src/mind/materialcompletion.js`, `src/mind/materiallifecycle.js`, `src/mind/perception.js`, `src/mind/perceptionattention.js`, `src/mind/perceptionexecutor.js`, `src/mind/portfolio.js`, `src/mind/social.js`, `src/mind/telegramdelivery.js`, `src/mind/toolinstaller.js`, `src/mind/tools.js`, `src/mind/understanding.js`, `src/mind/vision.js`, `src/mind/voicebridge.js`, `src/mind/work.js`, `src/mind/workcells.js`, `src/reminders.js`, `src/self/vault.js`, `src/web/httpdelivery.js`, `src/web/httpingress.js`, `src/web/httpresponseauthority.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema `aspirations`, `emotion_log`, `eva_conversation_opportunity_cursors`, `kv`, `memories`, `memory_review_events`, `messages`, `people`, `reminders`, `telegram_messages`, `timeline`, `turn_tool_executions`; leest `aspirations`, `emotion_log`, `eva_conversation_opportunity_cursors`, `kv`, `memories`, `memories_fts`, `memory_review_events`, `messages`, `people`, `reminders`, `sqlite_master`, `telegram_messages`, `timeline`, `turn_tool_executions`; schrijft `aspirations`, `emotion_log`, `eva_conversation_opportunity_cursors`, `kv`, `memories`, `memories_fts`, `memory_review_events`, `messages`, `people`, `reminders`, `telegram_messages`, `timeline`, `turn_tool_executions`; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert `chat`, `mood`, `timeline`.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `filesystem`, `sqlite`.

### `src/executionledger.js`

- Omvang: 549 regels, 24411 bytes. SHA-256 `0219d63292deff80aa86d8b49e62c849e34a5179bda84b3bf3507c2d3cd67aec`.
- Invoer uit lokale modules: geen.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `EXECUTION_RECORD_CONTRACT_V1` (value, regel 3), `EXECUTION_RECORD_CONTRACT_V2` (value, regel 4), `EXECUTION_VERIFICATION_VERIFIED` (value, regel 5), `EXECUTION_VERIFICATION_LEGACY_UNVERIFIABLE` (value, regel 6), `TURN_EFFECT_MANIFEST_TOOL` (value, regel 7), `TURN_EFFECT_PLAN_TOOL` (value, regel 8), `EXECUTION_LEDGER_TABLE_SQL` (value, regel 32), `EXECUTION_LEDGER_INDEX_SQL` (value, regel 82), `EXECUTION_LEDGER_TRIGGER_SQL` (value, regel 89), `EXECUTION_LEDGER_UPGRADE_PREDECESSOR_TRIGGER_SQL` (value, regel 156), `EXECUTION_LEDGER_COLUMN_LAYOUTS` (value, regel 229), `turnEffectManifestSpec` (function, regel 303), `turnEffectPlanSpec` (function, regel 324), `classifyTurnEffectManifest` (function, regel 370), `executionLedgerTriggersExact` (function, regel 494), `executionLedgerUpgradePredecessorTriggersExact` (function, regel 498), `executionLedgerSchemaManifest` (function, regel 506), `executionLedgerStatus` (function, regel 529), `requireExecutionLedger` (function, regel 544).
- Directe consumers: `src/db.js`, `src/mind/agent.js`, `src/mind/artifactcollections.js`, `src/mind/artifacts.js`, `src/mind/materiallifecycle.js`, `src/mind/workcompletion.js`, `src/turnauthority.js`, `src/web/httpingress.js`.
- Statisch zichtbare SQLite-identifiers: schema `turn_tool_executions`; leest `sqlite_master`, `sqlite_temp_master`; schrijft geen; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/index.js`

- Omvang: 127 regels, 4476 bytes. SHA-256 `465281cb39135b629e294dfa4045678ca624f6eec34cffc8b472680791b80b61`.
- Invoer uit lokale modules: `src/config.js`, `src/db.js`, `src/brain/oauth.js`, `src/integrations/homeassistant.js`, `src/integrations/telegram.js`, `src/web/server.js`, `src/mind/voicebridge.js`, `src/reminders.js`, `src/mind/autonomy.js`, `src/mind/perception.js`, `src/mind/emotions.js`, `src/mind/cognitive.js`, `src/mind/household.js`, `src/runtimeversion.js`, `src/self/vault.js`.
- Externe invoer/dependencies: `node:fs`, `node:path`, `node:child_process`.
- Uitvoer/API: geen exports; side-effect- of entrymodule.
- Directe consumers: geen.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `environment`, `filesystem`, `process-spawn`, `timer`.

### `src/integrations/agentmail.js`

- Omvang: 219 regels, 8713 bytes. SHA-256 `422befa42d5996e1e81bcca367abbb6167a7b868a97e9fed6037c62a88c03857`.
- Invoer uit lokale modules: `src/db.js`, `src/self/vault.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `agentMailStatus` (function, regel 64), `signUpAgentMail` (function, regel 78), `verifyAgentMail` (function, regel 131), `sendAgentMail` (function, regel 150), `listAgentMailMessages` (function, regel 184).
- Directe consumers: `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `timer`.

### `src/integrations/homeassistant.js`

- Omvang: 2358 regels, 95992 bytes. SHA-256 `0cfd676fccda144732c064f0281f22c6996cc19451c5a4f05ecb5b46b53ffcbb`.
- Invoer uit lokale modules: `src/config.js`, `src/bus.js`, `src/db.js`, `src/util.js`, `src/self/vault.js`, `src/mind/voicebridge.js`.
- Externe invoer/dependencies: `ws`, `node:crypto`, `node:dns/promises`, `node:fs/promises`, `node:https`, `node:path`.
- Uitvoer/API: `allStates` (function, regel 201), `haConnected` (function, regel 208), `cachedStates` (function, regel 214), `connectHA` (function, regel 220), `verifiedAuthenticatedHaVoiceEvent` (function, regel 339), `consumeAuthenticatedHaVoiceEvent` (function, regel 371), `listPersonConfigurations` (function, regel 487), `HA_INTEGRATION_SLUG_PATTERN` (value, regel 515), `listIntegrationInventory` (function, regel 551), `setPersonTrackers` (function, regel 727), `consumeIssuedCameraTriggerEvent` (function, regel 1211), `pendingIssuedCameraTriggerEvent` (function, regel 1220), `whoIsHome` (function, regel 1228), `presenceDetails` (function, regel 1234), `callService` (function, regel 1283), `restCall` (function, regel 1355), `getState` (function, regel 1384), `verifyEntityState` (function, regel 1483), `listCameras` (function, regel 1530), `findCamera` (function, regel 1570), `resolveDoorbellIntercomTarget` (function, regel 1601), `currentDoorbellIntercomTarget` (function, regel 1621), `announceOnDoorbellIntercom` (function, regel 1629), `cameraSnapshot` (function, regel 1888), `resolveVoiceAssistSatelliteTarget` (function, regel 1958), `voiceAssistSatelliteAnnouncementPlan` (function, regel 2008), `announceOnVoiceAssistSatellite` (function, regel 2030), `speak` (function, regel 2264).
- Directe consumers: `src/index.js`, `src/integrations/robot.js`, `src/mind/home.js`, `src/mind/perception.js`, `src/mind/perceptionattention.js`, `src/mind/perceptionexecutor.js`, `src/mind/tools.js`, `src/mind/vision.js`, `src/mind/voicebridge.js`, `src/reminders.js`, `src/voiceauthority.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert `ha_snapshot_ready`, `house`, `world_signal`.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `external-effect-call`, `filesystem`, `network`, `timer`.

### `src/integrations/robot.js`

- Omvang: 307 regels, 13295 bytes. SHA-256 `4b0f7d3540dd2e133109aa60a0ee09edbd76a1ffb43be5302d5a90cc8a3dc150`.
- Invoer uit lokale modules: `src/config.js`, `src/integrations/homeassistant.js`.
- Externe invoer/dependencies: `node:fs/promises`, `node:path`, `node:child_process`, `node:util`, `ffmpeg-static`.
- Uitvoer/API: `robotStatus` (function, regel 66), `robotMap` (function, regel 108), `captureRobotFrame` (function, regel 125), `controlRobot` (function, regel 145), `exploreWithRobot` (function, regel 192).
- Directe consumers: `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `filesystem`, `process-spawn`, `timer`.

### `src/integrations/telegram.js`

- Omvang: 3447 regels, 143726 bytes. SHA-256 `4875e605bbb55bbc7877dfbe2194f27136415c511437ff5bd8d9a9f81008563d`.
- Invoer uit lokale modules: `src/config.js`, `src/db.js`, `src/mind/portfolio.js`, `src/mind/tools.js`, `src/mind/vision.js`, `src/brain/llm.js`, `src/util.js`, `src/security/redaction.js`, `src/mind/runtimecontroller.js`, `src/mind/household.js`, `src/turnauthority.js`, `src/mind/agent.js`, `src/mind/agent.js`, `src/mind/agent.js`, `src/mind/agent.js`, `src/mind/agent.js`, `src/mind/agent.js`, `src/mind/agent.js`, `src/mind/agent.js`.
- Externe invoer/dependencies: `grammy`, `node:crypto`, `node:fs`, `node:os`, `node:path`, `node:child_process`, `node:util`, `node:perf_hooks`, `ffmpeg-static`.
- Uitvoer/API: `splitTelegramText` (function, regel 204), `verifiedTelegramRecoveryOriginalText` (function, regel 896), `verifiedTelegramUnderstandingSource` (function, regel 1397), `verifiedTelegramIdentityContext` (function, regel 1414), `verifiedTelegramDeliveryAuthority` (function, regel 1633), `consumeTelegramDeliveryAuthority` (function, regel 1645), `startTelegram` (function, regel 2349), `telegramPollingOptions` (function, regel 2719), `sendToFamily` (function, regel 3423), `sendPhotoToFamily` (function, regel 3430), `sendVoiceToFamily` (function, regel 3439).
- Directe consumers: `src/index.js`, `src/mind/agent.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest `eva_episode_origins`, `eva_turn_understanding_authorities_v1`, `telegram_messages`, `turn_tool_executions`; schrijft geen; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `external-effect-call`, `filesystem`, `network`, `process-spawn`, `sqlite`, `timer`.

### `src/mind/agent.js`

- Omvang: 10777 regels, 504611 bytes. SHA-256 `b1b87e3283784ac2e4e7aec22b9655a48af88e31cbc92d21516a3a0972f7357e`.
- Invoer uit lokale modules: `src/security/redaction.js`, `src/bus.js`, `src/db.js`, `src/config.js`, `src/brain/llm.js`, `src/mind/emotions.js`, `src/mind/persona.js`, `src/mind/tools.js`, `src/mind/home.js`, `src/mind/monitoringcontract.js`, `src/mind/perceptionattention.js`, `src/mind/signals.js`, `src/mind/understanding.js`, `src/mind/turnunderstandingoutcome.js`, `src/mind/turnplan.js`, `src/mind/work.js`, `src/mind/household.js`, `src/web/httpingress.js`, `src/mind/learning.js`, `src/mind/social.js`, `src/mind/communication.js`, `src/mind/cognitive.js`, `src/mind/curiosity.js`, `src/mind/runtimecontroller.js`, `src/mind/selfattentioncontext.js`, `src/mind/projectexecution.js`, `src/mind/materialcompletion.js`, `src/mind/artifacts.js`, `src/integrations/telegram.js`, `src/mind/voicebridge.js`, `src/turnauthority.js`, `src/voiceauthority.js`, `src/executionledger.js`, `src/mind/portfolio.js`, `src/util.js`.
- Externe invoer/dependencies: `node:perf_hooks`, `node:crypto`.
- Uitvoer/API: `recoverInterruptedTelegramUnderstandingIngress` (function, regel 6111), `inspectPreparedTelegramUnderstandingRecovery` (function, regel 6419), `preparedUnderstandingIngressReplay` (function, regel 6459), `verifiedTelegramDurableHandoffProjection` (function, regel 6504), `verifiedPreparedUnderstandingTurnForResult` (function, regel 6577), `verifiedVoiceFinalDeliveryRecoveryRequest` (function, regel 6607), `verifiedVoiceFinalDeliveryRecoveryForResult` (function, regel 6612), `handleMessageDetailed` (function, regel 10239), `recordDeliveredReply` (function, regel 10449), `deliveredReplyRecorded` (function, regel 10527), `runAutonomousTurn` (function, regel 10579), `agentStatus` (function, regel 10661).
- Directe consumers: `src/integrations/telegram.js`, `src/mind/autonomy.js`, `src/mind/voicebridge.js`, `src/voiceauthority.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest `aspirations`, `eva_episode_origins`, `eva_explorations`, `eva_work_items`, `people`, `telegram_messages`, `timeline`, `turn_tool_executions`; schrijft `turn_tool_executions`; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert `activity`.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `sqlite`.

### `src/mind/appearance.js`

- Omvang: 18 regels, 645 bytes. SHA-256 `77e95d350a277382c002c01b5628e2d5c6839ee3b609755a5d76cc73154f5196`.
- Invoer uit lokale modules: `src/db.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `getAppearance` (function, regel 15).
- Directe consumers: `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: geen marker.

### `src/mind/artifactcollections.js`

- Omvang: 3147 regels, 149131 bytes. SHA-256 `4baebc57ee3953a04fb72b3b323a8bec8935b8c790aa08ec5cbcdfb60d5292e8`.
- Invoer uit lokale modules: `src/config.js`, `src/db.js`, `src/executionledger.js`, `src/mind/artifacts.js`.
- Externe invoer/dependencies: `node:crypto`, `node:fs`, `node:path`.
- Uitvoer/API: `createArtifactCollectionStore` (function, regel 2684), `artifactCollectionContracts` (value, regel 3126).
- Directe consumers: geen.
- Statisch zichtbare SQLite-identifiers: schema `eva_artifact_collection_checks`, `eva_artifact_collection_consumptions`, `eva_artifact_collection_members`, `eva_artifact_collections`, `eva_recursive_workspace_snapshot_members`, `eva_recursive_workspace_snapshots`; leest `eva_episode_origins`, `eva_goal_episodes`, `eva_work_items`, `sqlite_master`, `sqlite_temp_master`, `timeline`, `turn_tool_executions`; schrijft geen; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `filesystem`.

### `src/mind/artifacts.js`

- Omvang: 1541 regels, 69510 bytes. SHA-256 `df598fc41d977694babb758f37a3b6ebe4aba6b11f3834756c85f1595a4e1ae9`.
- Invoer uit lokale modules: `src/db.js`, `src/executionledger.js`, `src/util.js`, `src/mind/projectexecution.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `artifactVersionStoreStatus` (function, regel 516), `registerArtifactSealAuthorityVerifier` (function, regel 529), `artifactSealAuthority` (function, regel 635), `storeWorkspaceArtifactObject` (function, regel 739), `sealVerifiedWorkArtifacts` (function, regel 1197), `artifactVersion` (function, regel 1355), `artifactVersionReceipt` (function, regel 1364), `artifactVersionsForWork` (function, regel 1375), `artifactVersionsForEpisode` (function, regel 1384), `artifactBytes` (function, regel 1395), `artifactCollectionStoreAuthority` (function, regel 1495), `consumeArtifactCollectionStoreAuthority` (function, regel 1517), `artifactVersionContracts` (value, regel 1532).
- Directe consumers: `src/mind/agent.js`, `src/mind/artifactcollections.js`, `src/mind/materiallifecycle.js`, `src/mind/portfolio.js`.
- Statisch zichtbare SQLite-identifiers: schema `eva_artifact_objects`, `eva_artifact_seals`, `eva_artifact_versions`; leest `eva_artifact_objects`, `eva_artifact_seals`, `eva_artifact_versions`, `eva_goal_episodes`, `eva_work_items`, `sqlite_master`, `sqlite_temp_master`, `timeline`, `turn_tool_executions`; schrijft `eva_artifact_objects`, `eva_artifact_seals`, `eva_artifact_versions`; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/mind/autonomy.js`

- Omvang: 1047 regels, 43232 bytes. SHA-256 `1839322311f75ff290f6bbae84c90d2041bfaf016f2d923115d936e98679133c`.
- Invoer uit lokale modules: `src/config.js`, `src/bus.js`, `src/db.js`, `src/util.js`, `src/mind/agent.js`, `src/mind/perceptionexecutor.js`, `src/mind/work.js`, `src/mind/curiosity.js`, `src/mind/portfolio.js`, `src/mind/runtimecontroller.js`, `src/mind/signals.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `shouldQueueWorldSignal` (function, regel 567), `shouldWakeForWorldSignal` (function, regel 596), `hasNightUrgentSignal` (function, regel 603), `startAutonomy` (function, regel 973), `autonomyStatus` (function, regel 996), `__autonomyScheduleTest` (value, regel 1016).
- Directe consumers: `src/index.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest `aspirations`, `eva_explorations`, `eva_goals`, `timeline`; schrijft geen.
- Statisch letterlijke bus-events: ontvangt `house`, `perception_ready`, `work_changed`, `world_signal`; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `sqlite`, `timer`.

### `src/mind/capabilities.js`

- Omvang: 387 regels, 17752 bytes. SHA-256 `86f442ebae51f447ece404c939bd6eebbcd981e1f0ca303f744462e8b560f105`.
- Invoer uit lokale modules: `src/db.js`, `src/config.js`, `src/self/vault.js`.
- Externe invoer/dependencies: `node:fs`, `node:path`, `node:crypto`.
- Uitvoer/API: `stageSkill` (function, regel 82), `getCapability` (function, regel 149), `stageAddon` (function, regel 155), `setCapabilityStatus` (function, regel 193), `capabilityTestSpec` (function, regel 206), `issueCapabilityReceipt` (function, regel 223), `activateCapability` (function, regel 254), `useActiveCapability` (function, regel 279), `listCapabilities` (function, regel 313), `capabilityInventory` (function, regel 336), `capabilityContextText` (function, regel 370).
- Directe consumers: `src/mind/tools.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema `eva_capabilities`, `eva_capability_receipts`; leest `eva_capabilities`, `eva_capability_receipts`; schrijft `eva_capabilities`, `eva_capability_receipts`; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `filesystem`, `sqlite`.

### `src/mind/cognitive.js`

- Omvang: 829 regels, 30278 bytes. SHA-256 `265da65a0ec4dec0d6dc5c673fadb1b14eb02a969cc0d467412132a7b21878e4`.
- Invoer uit lokale modules: `src/bus.js`, `src/db.js`, `src/util.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `cognitiveState` (function, regel 184), `ingestCognitiveEvent` (function, regel 204), `beginCognitiveEpisode` (function, regel 281), `recordCognitiveAction` (function, regel 407), `recordCognitiveOutcome` (function, regel 458), `closeCognitiveEpisode` (function, regel 519), `recentFreeNoActionContextText` (function, regel 693), `cognitiveContextText` (function, regel 726), `startCognitiveCore` (function, regel 759).
- Directe consumers: `src/index.js`, `src/mind/agent.js`.
- Statisch zichtbare SQLite-identifiers: schema `cognitive_episodes`, `cognitive_events`, `cognitive_state`; leest `cognitive_episodes`, `cognitive_events`, `cognitive_state`; schrijft `cognitive_episodes`, `cognitive_events`, `cognitive_state`.
- Statisch letterlijke bus-events: ontvangt `ha_snapshot_ready`, `house`, `world_signal`; emitteert `cognitive_state`.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `sqlite`.

### `src/mind/communication.js`

- Omvang: 830 regels, 39426 bytes. SHA-256 `93cd1ac404be339ff940874295fe9a13e1b945316b5f5ad21606e4c773ef868f`.
- Invoer uit lokale modules: `src/db.js`, `src/util.js`, `src/mind/home.js`, `src/mind/turnplan.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `boundedVerifiedHomePartialEffect` (function, regel 161), `homeReadbackProvesIntent` (function, regel 181), `homeEffectClaimFeedback` (function, regel 650), `isScheduledMonitoringItem` (function, regel 685), `scheduledMonitoringWork` (function, regel 702), `turnMonitoringProofOptions` (function, regel 742), `monitoringPromiseFeedback` (function, regel 762), `selfRepairClaimFeedback` (function, regel 770), `autonomousMessageNeedsAccountability` (function, regel 787), `autonomousMessageNeedsRewrite` (function, regel 791), `routineAutonomousMessageCoolingDown` (function, regel 803), `reviewAutonomousMessage` (function, regel 818).
- Directe consumers: `src/mind/agent.js`, `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `sqlite`.

### `src/mind/curiosity.js`

- Omvang: 1842 regels, 89488 bytes. SHA-256 `01f9d00983dc178840aab9d39188c0f831726803202e6ac062341589f1c06a7b`.
- Invoer uit lokale modules: `src/db.js`, `src/bus.js`, `src/config.js`, `src/security/redaction.js`, `src/util.js`, `src/mind/portfolio.js`, `src/mind/learning.js`, `src/mind/selfattentioncontext.js`, `src/mind/understanding.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `explorationEvidenceTopicSha256` (function, regel 53), `registerExplorationEvidenceReceiptVerifier` (function, regel 61), `explorationById` (function, regel 227), `listExplorations` (function, regel 231), `startExploration` (function, regel 246), `issueFreeAutonomyExplorationAtomicStart` (function, regel 389), `startIssuedFreeAutonomyExplorationAtomic` (function, regel 432), `updateExploration` (function, regel 528), `completeIssuedExplorationContribution` (function, regel 1090), `noteExplorationAction` (function, regel 1301), `noteIssuedExplorationAction` (function, regel 1305), `attentionContextText` (function, regel 1343), `sleepingExplorationConstraintText` (function, regel 1402), `dueAttention` (function, regel 1746), `nextDeferredExplorationRetryTs` (function, regel 1783), `settleIssuedExplorationAttention` (function, regel 1792), `settleIssuedAspirationAttention` (function, regel 1825), `__curiosityTest` (value, regel 1837).
- Directe consumers: `src/mind/agent.js`, `src/mind/autonomy.js`, `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema `eva_explorations`; leest `aspirations`, `cognitive_episodes`, `eva_capabilities`, `eva_explorations`, `eva_goals`, `eva_projects`, `eva_tool_installations`, `eva_work_items`, `timeline`; schrijft `aspirations`, `eva_explorations`; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert `exploration_changed`, `work_changed`.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `sqlite`.

### `src/mind/economichandoff.js`

- Omvang: 94 regels, 4730 bytes. SHA-256 `30afabb9e902d1c4d854ae39bb29f73009cbdbb060ba096eafa133368433e9e5`.
- Invoer uit lokale modules: `src/db.js`, `src/util.js`, `src/mind/household.js`, `src/mind/portfolio.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `explorationEconomicWorkBoundary` (function, regel 13), `explorationEconomicExternalPlanAllowed` (function, regel 54), `explorationEconomicFamilyDelivery` (function, regel 64), `explorationEconomicExecutionPolicy` (function, regel 81).
- Directe consumers: `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest `eva_work_items`; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `sqlite`.

### `src/mind/embeddings.js`

- Omvang: 16 regels, 724 bytes. SHA-256 `7faee071ab497d2bb844fe174a5aaf99ab108dc5f39692ba386cd7f3f98ff594`.
- Invoer uit lokale modules: geen.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `vecToBuf` (value, regel 3), `bufToVec` (value, regel 7).
- Directe consumers: `src/mind/facerec.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: geen marker.

### `src/mind/emotions.js`

- Omvang: 159 regels, 5900 bytes. SHA-256 `129d1de4af7633611eaa42e7307067d5ebd82186ade7c5e3e7c5d1d148e60a3d`.
- Invoer uit lokale modules: `src/bus.js`, `src/db.js`, `src/util.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `getMood` (function, regel 11), `nudge` (function, regel 21), `setMood` (function, regel 29), `applyEvent` (function, regel 52), `appraiseGroundedRegulation` (function, regel 62), `decayTick` (function, regel 83), `moodSummary` (function, regel 94), `moodActionGuidance` (function, regel 106), `interactionFeedback` (function, regel 122), `applyInteractionFeedback` (function, regel 134), `startEmotionalLife` (function, regel 148).
- Directe consumers: `src/index.js`, `src/mind/agent.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt `cognitive_state`; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `timer`.

### `src/mind/facerec.js`

- Omvang: 269 regels, 12226 bytes. SHA-256 `60c40daa040eac2b340c95139bbccda5a3b18577d2987113fd6d7357be233f1b`.
- Invoer uit lokale modules: `src/db.js`, `src/mind/embeddings.js`, `src/mind/localbrain.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `detectFaces` (function, regel 78), `learnFace` (function, regel 113), `recognizeFaceDetailed` (function, regel 167), `warmupFaceRec` (function, regel 259).
- Directe consumers: `src/mind/perception.js`, `src/mind/vision.js`.
- Statisch zichtbare SQLite-identifiers: schema `faces`; leest `faces`; schrijft `faces`.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `sqlite`, `timer`.

### `src/mind/home.js`

- Omvang: 1585 regels, 69184 bytes. SHA-256 `4098496c44737545d4f78da3cb58a6ea9ddbdae4265dfdf439bed52176eb67db`.
- Invoer uit lokale modules: `src/db.js`, `src/integrations/homeassistant.js`, `src/util.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `compactHomeState` (function, regel 171), `bindHomeContextEntityReferences` (function, regel 544), `homeContextText` (function, regel 828), `validHomeRequesterTargetBinding` (function, regel 1018), `resolveHomeEntity` (function, regel 1165), `resolveContextualHomeCommand` (function, regel 1301), `groundContextualHomeCommand` (function, regel 1355), `homeOperationOutcome` (function, regel 1376), `operateHome` (function, regel 1531).
- Directe consumers: `src/mind/agent.js`, `src/mind/communication.js`, `src/mind/portfolio.js`, `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `network`.

### `src/mind/household.js`

- Omvang: 449 regels, 16416 bytes. SHA-256 `71f9c9ce3b00f5b6ff669bcca14286534593ec2b4e751967680d6746386cbc64`.
- Invoer uit lokale modules: `src/db.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `RELATIONSHIP_FEEDBACK_KINDS` (value, regel 13), `classifyPerson` (function, regel 132), `classifiedPeople` (function, regel 146), `householdMembers` (function, regel 151), `syncHouseholdRolesFromPresence` (function, regel 162), `setPersonHouseholdRole` (function, regel 192), `recordRelationshipFeedback` (function, regel 251), `relationshipFeedbackForPerson` (function, regel 307), `householdContextText` (function, regel 377).
- Directe consumers: `src/index.js`, `src/integrations/telegram.js`, `src/mind/agent.js`, `src/mind/economichandoff.js`, `src/mind/learning.js`, `src/mind/perceptionexecutor.js`, `src/mind/telegramdelivery.js`, `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema `household_person_roles`, `relationship_feedback`; leest `household_person_roles`, `people`, `relationship_feedback`; schrijft `household_person_roles`, `relationship_feedback`; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `sqlite`.

### `src/mind/learning.js`

- Omvang: 1304 regels, 64512 bytes. SHA-256 `b15e8affbe1f995f649789dd0cbcd487b40c9ba29b120a5d71850c63427841ab`.
- Invoer uit lokale modules: `src/db.js`, `src/security/redaction.js`, `src/util.js`, `src/mind/household.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `behaviorLearningAvailable` (function, regel 373), `renderBehaviorLensPrompt` (function, regel 432), `normalizeBehaviorLensCandidate` (function, regel 459), `registerBehaviorProposalVerifier` (function, regel 504), `registerBehaviorFeedbackVerifier` (function, regel 511), `registerBehaviorDeliveryVerifier` (function, regel 518), `materializeBehaviorExperiment` (function, regel 611), `behaviorProposalAttestation` (function, regel 677), `replayBehaviorExperiment` (function, regel 700), `expirePreparedBehaviorCanaries` (function, regel 819), `claimBehaviorCanaryForPerson` (function, regel 885), `behaviorCanaryLens` (function, regel 893), `releaseBehaviorCanaryClaim` (function, regel 906), `prepareBehaviorCanaryDelivery` (function, regel 915), `behaviorCanaryDeliveryMarker` (function, regel 973), `recordBehaviorCanaryExposure` (function, regel 986), `settleBehaviorCanaryInconclusive` (function, regel 1118), `settleBehaviorCanaryFromFeedback` (function, regel 1182), `activeBehaviorLensContext` (function, regel 1274), `behaviorLearningStatus` (function, regel 1282), `BEHAVIOR_LEARNING_CONTRACTS` (value, regel 1300).
- Directe consumers: `src/mind/agent.js`, `src/mind/curiosity.js`, `src/mind/tools.js`, `src/mind/understanding.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema `eva_behavior_canary_deliveries`, `eva_behavior_experiment_events`, `eva_behavior_experiments`, `eva_behavior_feedback_settlements`; leest `eva_behavior_canary_deliveries`, `eva_behavior_experiment_events`, `eva_behavior_experiments`, `eva_behavior_feedback_settlements`, `people`, `relationship_feedback`, `sqlite_master`; schrijft `eva_behavior_canary_deliveries`, `eva_behavior_experiment_events`, `eva_behavior_experiments`, `eva_behavior_feedback_settlements`; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/mind/localbrain.js`

- Omvang: 135 regels, 4263 bytes. SHA-256 `ebe0047b5733074cafa84d6161fa405b0af777bf81b4ab12c1ed97a7e7bf8c50`.
- Invoer uit lokale modules: `src/config.js`, `src/db.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `isRemoteBrain` (function, regel 37), `brainServiceStatus` (function, regel 69), `brainPost` (function, regel 88).
- Directe consumers: `src/mind/facerec.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `network`.

### `src/mind/materialcompletion.js`

- Omvang: 743 regels, 31001 bytes. SHA-256 `afc24d61d50632736430d262d0307818193fa8ccb284f413a1239cb6f49edbb3`.
- Invoer uit lokale modules: `src/db.js`, `src/util.js`, `src/mind/projectexecution.js`, `src/mind/workspacefilemanifest.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `materialRequestSentinel` (function, regel 116), `materialCompletionObligation` (function, regel 241), `sealMaterialTargetManifest` (function, regel 424), `durableMaterialProof` (function, regel 573), `fixedMaterialIncompleteReply` (function, regel 728), `fixedMaterialAuthorizationReply` (function, regel 740).
- Directe consumers: `src/mind/agent.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/mind/materiallifecycle.js`

- Omvang: 3888 regels, 183357 bytes. SHA-256 `c417f271bd91b2777ccb86a305cc43825ca870f9c8b7aa9c3462f01d56435ec5`.
- Invoer uit lokale modules: `src/config.js`, `src/db.js`, `src/executionledger.js`, `src/turnauthority.js`, `src/util.js`, `src/mind/artifacts.js`, `src/mind/workcompletion.js`.
- Externe invoer/dependencies: `node:crypto`, `node:fs`, `node:path`.
- Uitvoer/API: `materialLifecycleStoreStatus` (function, regel 1396), `registerMaterialLifecycleAuthorityVerifier` (function, regel 1418), `registerMaterialEffectRecoveryVerifier` (function, regel 1422), `issueMaterialExecutionContractCapability` (function, regel 2389), `materialExecutionContract` (function, regel 2562), `materialExecutionContractByHandoff` (function, regel 2567), `bindMaterialExecutionContract` (function, regel 2615), `issueMaterialEffectIntentCapability` (function, regel 2952), `materialEffectIntent` (function, regel 3134), `appendMaterialEffectIntent` (function, regel 3181), `materialEffectWriteClaim` (function, regel 3315), `issueMaterialEffectWriteClaimCapability` (function, regel 3322), `claimMaterialEffectWrite` (function, regel 3364), `materialEffectWriteFenceCurrent` (function, regel 3441), `issueMaterialEffectReconciliationCapability` (function, regel 3564), `materialEffectReconciliation` (function, regel 3785), `reconcileMaterialEffect` (function, regel 3832), `materialLifecycleContracts` (value, regel 3882).
- Directe consumers: geen.
- Statisch zichtbare SQLite-identifiers: schema `eva_interpretation_retry_handoffs`, `eva_material_effect_claims`, `eva_material_effect_intents`, `eva_material_effect_reconciliations`, `eva_material_execution_contracts`, `eva_material_execution_observations`; leest `eva_artifact_versions`, `eva_episode_origins`, `eva_goal_episodes`, `eva_interpretation_retry_handoffs`, `eva_material_effect_claims`, `eva_material_effect_intents`, `eva_material_effect_reconciliations`, `eva_material_execution_contracts`, `eva_material_execution_observations`, `eva_work_items`, `messages`, `sqlite_master`, `telegram_messages`, `timeline`, `turn_tool_executions`; schrijft geen; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `filesystem`.

### `src/mind/monitoringcontract.js`

- Omvang: 1472 regels, 68910 bytes. SHA-256 `f70ec3e0895d68556538441d3be6a687c0650c7b6e9ac03aa702689febfb2e58`.
- Invoer uit lokale modules: geen.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `monitoringConditionId` (function, regel 161), `monitoringContractReadBindings` (function, regel 205), `normalizeMonitoringFactBinding` (function, regel 210), `issueMonitoringFactBinding` (function, regel 251), `buildMonitoringMeasurementFact` (function, regel 314), `normalizeMonitoringMeasurementFact` (function, regel 407), `normalizeMonitoringOutcomeDecision` (function, regel 435), `monitoringOutcomeRequestRecord` (function, regel 449), `monitoringOutcomeResponseRecord` (function, regel 482), `renderMonitoringTerminalText` (function, regel 508), `issueMonitoringPresentationContext` (function, regel 537), `normalizeMonitoringPresentationContext` (function, regel 553), `issueMonitoringDeliveryBinding` (function, regel 632), `normalizeMonitoringDeliveryBinding` (function, regel 670), `issueMonitoringContract` (function, regel 732), `normalizeMonitoringContract` (function, regel 812), `monitoringDeliveryMatchesAuthority` (function, regel 846), `monitoringStateFitsDurableBudget` (function, regel 855), `issueMonitoringLifecycleBinding` (function, regel 956), `normalizeMonitoringLifecycleBinding` (function, regel 1006), `issueMonitoringOutcomeReceipt` (function, regel 1069), `normalizeMonitoringOutcomeReceipt` (function, regel 1103), `monitoringPendingFactReferences` (function, regel 1201), `issueMonitoringPendingDelivery` (function, regel 1234), `normalizeMonitoringPendingDeliveryReference` (function, regel 1342), `normalizeMonitoringPendingDelivery` (function, regel 1365), `monitoringDeliveryTransportLimits` (function, regel 1422), `monitoringDeliveryRetrySchedule` (function, regel 1431), `monitoringDeliveryWireCurrent` (function, regel 1461).
- Directe consumers: `src/mind/agent.js`, `src/mind/portfolio.js`, `src/mind/tools.js`, `src/mind/understanding.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/mind/perception.js`

- Omvang: 449 regels, 17516 bytes. SHA-256 `f6797337059ebf4bb2443463bc610cb8578158e8486a397fdca94227cca24169`.
- Invoer uit lokale modules: `src/bus.js`, `src/config.js`, `src/db.js`, `src/integrations/homeassistant.js`, `src/mind/facerec.js`, `src/mind/tools.js`, `src/mind/persona.js`, `src/mind/perceptionattention.js`.
- Externe invoer/dependencies: `node:fs/promises`, `node:path`.
- Uitvoer/API: `cleanupTemporaryCameraFrames` (function, regel 77), `cameraReportHistory` (function, regel 97), `cameraEventIsFresh` (function, regel 106), `selectCameraEventForAttention` (function, regel 116), `shouldReportCameraEvent` (function, regel 195), `cameraNoticeRecognition` (function, regel 221), `cameraAttentionAudit` (function, regel 232), `cameraFamilyCaption` (function, regel 242), `applyVisibleCameraDeliveryState` (function, regel 263), `startPerception` (function, regel 432).
- Directe consumers: `src/index.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt `house`; emitteert `perception_ready`.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `filesystem`.

### `src/mind/perceptionattention.js`

- Omvang: 988 regels, 47534 bytes. SHA-256 `1a7614cd12a72d7c96e1e61af15d8289158690ba59226950572b19714483c74f`.
- Invoer uit lokale modules: `src/config.js`, `src/db.js`, `src/integrations/homeassistant.js`, `src/security/redaction.js`, `src/util.js`.
- Externe invoer/dependencies: `node:fs`, `node:path`, `node:crypto`.
- Uitvoer/API: `cameraAttentionFingerprint` (function, regel 59), `selectCameraAttentionCandidate` (function, regel 101), `replacePendingCameraCandidate` (function, regel 177), `verifiedDoorbellTelegramDeliveryEvidence` (function, regel 455), `perceptionAttentionAvailable` (function, regel 518), `recordDoorbellEvidence` (function, regel 525), `claimReadyPerceptionIncident` (function, regel 646), `perceptionIncidentContext` (function, regel 687), `perceptionIncidentDecisionFrame` (function, regel 692), `normalizePerceptionDecision` (function, regel 699), `issuePerceptionIncidentPlan` (function, regel 720), `beginPerceptionIncidentEffect` (function, regel 774), `settlePerceptionIncidentPlan` (function, regel 798), `failPerceptionIncidentClaim` (function, regel 836), `recoverPerceptionIncidents` (function, regel 858), `claimPlannedPerceptionIncident` (function, regel 890), `cleanupPerceptionFrames` (function, regel 902), `migrateLegacyDoorbellFrameDirectory` (function, regel 951), `perceptionAttentionStatus` (function, regel 978), `PERCEPTION_ATTENTION_CONTRACTS` (value, regel 986).
- Directe consumers: `src/mind/agent.js`, `src/mind/perception.js`, `src/mind/perceptiondecision.js`, `src/mind/perceptionexecutor.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema `eva_doorbell_events`, `eva_doorbell_incidents`, `eva_doorbell_plans`, `eva_doorbell_sources`; leest `eva_doorbell_incidents`, `eva_doorbell_plans`, `eva_doorbell_sources`, `sqlite_master`; schrijft `eva_doorbell_events`, `eva_doorbell_incidents`, `eva_doorbell_plans`, `eva_doorbell_sources`.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `filesystem`.

### `src/mind/perceptiondecision.js`

- Omvang: 104 regels, 4224 bytes. SHA-256 `e860e3a8c932da3be967d14b750dac221c51ce406d4bbc67463022fc40acb8d4`.
- Invoer uit lokale modules: `src/config.js`, `src/brain/llm.js`, `src/mind/persona.js`, `src/mind/perceptionattention.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `decidePerceptionIncident` (function, regel 53), `__perceptionDecisionTest` (value, regel 103).
- Directe consumers: `src/mind/perceptionexecutor.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: geen marker.

### `src/mind/perceptionexecutor.js`

- Omvang: 148 regels, 5888 bytes. SHA-256 `7fe59797d60367c4f9f2ae8d66437d5b47ccf749312ef7abc03f026656c24733`.
- Invoer uit lokale modules: `src/db.js`, `src/integrations/homeassistant.js`, `src/mind/household.js`, `src/mind/perceptionattention.js`, `src/mind/perceptiondecision.js`, `src/mind/tools.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `runPerceptionIncidentOnce` (function, regel 91), `perceptionIncidentPending` (function, regel 133), `initializePerceptionIncidentRuntime` (function, regel 140), `__perceptionExecutorTest` (value, regel 147).
- Directe consumers: `src/mind/autonomy.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `external-effect-call`.

### `src/mind/persona.js`

- Omvang: 213 regels, 14881 bytes. SHA-256 `e9e266adc0209dcbad2b0529ea5523a86f4fb8dd6ec6f67dc00ae887a522ac65`.
- Invoer uit lokale modules: `src/util.js`, `src/config.js`, `src/security/redaction.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `EVA_CHARACTER_CORE` (value, regel 5), `EVA_VOICE` (value, regel 13), `EVA_CHAT_VOICE` (value, regel 23), `EVA_SPOKEN_VOICE` (value, regel 27), `EVA_AUTONOMOUS_VOICE` (value, regel 29), `EVA_STYLE_REMINDER` (value, regel 31), `semanticFailurePresentation` (function, regel 52), `semanticFailureReplyFeedback` (function, regel 67), `renderHouseActionResult` (function, regel 130), `renderCameraNotice` (function, regel 152), `renderReminderText` (function, regel 165), `buildInstructions` (function, regel 171), `buildRedactedInstructions` (value, regel 210), `default` (default, regel 212).
- Directe consumers: `src/mind/agent.js`, `src/mind/perception.js`, `src/mind/perceptiondecision.js`, `src/reminders.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: geen marker.

### `src/mind/portfolio.js`

- Omvang: 5177 regels, 239172 bytes. SHA-256 `22f5787c7d9006c344df37a2bccf5ae279a86eaed81dc91b14548b50bc903cfd`.
- Invoer uit lokale modules: `src/db.js`, `src/turnauthority.js`, `src/voiceauthority.js`, `src/security/redaction.js`, `src/util.js`, `src/mind/understanding.js`, `src/mind/home.js`, `src/mind/work.js`, `src/mind/artifacts.js`, `src/mind/monitoringcontract.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `EPISODE_TRANSITIONS` (value, regel 696), `materializeExplorationEconomicHandoff` (function, regel 961), `replayExplorationEconomicHandoff` (function, regel 1083), `listGoals` (function, regel 1095), `dueGoalAttention` (function, regel 1102), `manageGoal` (function, regel 1202), `goalEpisodeById` (function, regel 1230), `attachEpisodeOrigin` (function, regel 1339), `createGoalEpisode` (function, regel 1416), `bindEpisodeToWork` (function, regel 1479), `ensureEpisodeForWorkItem` (function, regel 1501), `createDurableWork` (function, regel 1562), `recoverInterruptedUnderstandingIngresses` (function, regel 1895), `interpretationRetryResume` (function, regel 1902), `abandonInterpretationRetry` (function, regel 1925), `interpretationReplyPreparationBinding` (function, regel 1938), `verifyInterpretationRetryPendingDelivery` (function, regel 2090), `interpretationReplyDeliveryGate` (function, regel 2108), `resolveInterpretationRetryFailure` (function, regel 2161), `completeInterpretationRetry` (function, regel 2240), `registeredInterpretationRetryHandoffForOrigin` (function, regel 2588), `createInterpretationRetryHandoff` (function, regel 2732), `cancelInterpretationRetryForConversation` (function, regel 2841), `adoptUnboundWorkEpisodes` (function, regel 2850), `recoverStrandedDeliveryWork` (function, regel 2904), `freezeEpisodeDelivery` (function, regel 2958), `reconcileFrozenEpisodeDeliveries` (function, regel 3013), `episodeForWork` (function, regel 3045), `openEpisodeForConversation` (function, regel 3054), `conversationWorkReceipt` (function, regel 3069), `recentEpisodeArtifactReceipts` (function, regel 3100), `episodeDeliveryOrigin` (function, regel 3132), `transitionGoalEpisode` (function, regel 3449), `updateDurableWork` (function, regel 3456), `updateGoalEpisode` (function, regel 3482), `reconcileTerminalProjects` (function, regel 3486), `requiredTelegramOriginBinding` (function, regel 3530), `verifyMonitoringLifecycleAuthority` (function, regel 4229), `acceptedMonitoringDeliveryReceiptCandidate` (function, regel 4251), `reconcileAcceptedMonitoringDelivery` (function, regel 4310), `verifiedTelegramFinalDeliveryOrigin` (function, regel 4518), `sealFinalDeliveryReply` (function, regel 4696), `recordEpisodeDelivery` (function, regel 4958), `recoverDeliveredEpisodeReceipts` (function, regel 5013), `episodeNeedsDelivery` (function, regel 5054), `episodeHasDelivery` (function, regel 5060), `episodeHasFinalDelivery` (function, regel 5065), `createAutoBoundTurnWork` (function, regel 5078), `oneShotAutoBoundMemoryContribution` (function, regel 5106), `selectPortfolioCommitment` (function, regel 5134).
- Directe consumers: `src/integrations/telegram.js`, `src/mind/agent.js`, `src/mind/autonomy.js`, `src/mind/curiosity.js`, `src/mind/economichandoff.js`, `src/mind/tools.js`, `src/mind/voicebridge.js`.
- Statisch zichtbare SQLite-identifiers: schema `eva_delivery_receipts`, `eva_episode_events`, `eva_episode_origins`, `eva_execution_effect_receipts`, `eva_execution_intent_heads`, `eva_goal_episodes`, `eva_goals`, `eva_projects`; leest `eva_delivery_receipts`, `eva_episode_events`, `eva_episode_origins`, `eva_execution_intent_heads`, `eva_explorations`, `eva_goal_episodes`, `eva_goals`, `eva_interpretation_retry_handoffs`, `eva_projects`, `eva_work_items`, `kv`, `messages`, `sqlite_master`, `telegram_messages`, `timeline`, `turn_tool_executions`; schrijft `eva_delivery_receipts`, `eva_episode_events`, `eva_episode_origins`, `eva_execution_intent_heads`, `eva_goal_episodes`, `eva_goals`, `eva_interpretation_retry_handoffs`, `eva_projects`, `eva_work_items`, `kv`, `telegram_messages`; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `sqlite`.

### `src/mind/projectexecution.js`

- Omvang: 508 regels, 21843 bytes. SHA-256 `2c6e07237c92e949682de494954964504fad661f82fe836974b48dc4cbe8d639`.
- Invoer uit lokale modules: `src/config.js`, `src/util.js`.
- Externe invoer/dependencies: `node:path`.
- Uitvoer/API: `cleanUserText` (function, regel 8), `actionData` (function, regel 16), `canonicalArtifactDisplayPath` (function, regel 27), `writtenArtifactPath` (function, regel 50), `readArtifactPath` (function, regel 56), `executedArtifactPath` (function, regel 60), `isTestArtifactPath` (function, regel 66), `successfulZeroExit` (function, regel 80), `executableWorkspaceArtifact` (function, regel 91), `iterativeArtifactStatus` (function, regel 126), `iterativeArtifactEvidence` (function, regel 223), `iterativeExecutionObligation` (function, regel 247), `programmingAcceptanceStatus` (function, regel 306), `completedArtifact` (function, regel 338), `pendingExecutableArtifactPath` (function, regel 379), `buildMissionComplete` (function, regel 390), `phasedProgrammingBuildStatus` (function, regel 400), `issuedExecutionTraceMetadata` (function, regel 477).
- Directe consumers: `src/mind/agent.js`, `src/mind/artifacts.js`, `src/mind/materialcompletion.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`.

### `src/mind/runtimecontroller.js`

- Omvang: 599 regels, 31474 bytes. SHA-256 `c992667fbe44dea9671e5cca37f753309a7d8cbd171d311f8fff2c58a399b3ad`.
- Invoer uit lokale modules: `src/config.js`, `src/runtimeversion.js`, `src/mind/selfattentioncontext.js`, `deploy/eva-runtime-repair-protocol.mjs`.
- Externe invoer/dependencies: `node:fs`, `node:path`, `node:child_process`, `node:crypto`.
- Uitvoer/API: `registerRuntimePrincipalValidator` (function, regel 60), `runtimePrincipalAuthorized` (function, regel 73), `issueUnprivilegedAutonomyContext` (function, regel 79), `verifiedAutonomyInteractionEpoch` (function, regel 94), `issueAutonomySelfAttentionReceipt` (function, regel 188), `issueAutonomySelfAttentionIngress` (function, regel 192), `verifiedAutonomySelfAttentionReceipt` (function, regel 196), `createRuntimeController` (function, regel 441), `manageRuntime` (value, regel 594), `__runtimeControllerTest` (value, regel 595).
- Directe consumers: `src/integrations/telegram.js`, `src/mind/agent.js`, `src/mind/autonomy.js`, `src/mind/tools.js`, `src/mind/understanding.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `filesystem`, `process-spawn`.

### `src/mind/selfattentioncontext.js`

- Omvang: 156 regels, 7099 bytes. SHA-256 `0ebbfedd681ee3ff7981d2c6756773c9a07b209a383f949bf4e6f77b3a0941bc`.
- Invoer uit lokale modules: `src/security/redaction.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `EXPLORATION_CONTINUATION_CONTRACT` (value, regel 4), `validExplorationContinuationContext` (function, regel 53), `createIssuedExplorationAttention` (function, regel 110), `issuedExplorationContinuationContext` (function, regel 120), `providerExplorationContinuationContext` (function, regel 126).
- Directe consumers: `src/mind/agent.js`, `src/mind/curiosity.js`, `src/mind/runtimecontroller.js`, `src/mind/understanding.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/mind/signals.js`

- Omvang: 217 regels, 9212 bytes. SHA-256 `7d2481a5060c0919908cb2edee00170f3cbacb0dd9dcd6e7ade86b0b3c7ae721`.
- Invoer uit lokale modules: `src/util.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `SIGNAL_BATCH_CONTRACT` (value, regel 4), `SIGNAL_ASSESSMENT_CONTRACT` (value, regel 5), `SIGNAL_QUARANTINE_CONTRACT` (value, regel 6), `SIGNAL_QUARANTINE_LIMIT` (value, regel 7), `SIGNAL_BATCH_MAX_ENTITY_KEYS` (value, regel 11), `HOME_ENTITY_SIGNAL_SCOPES` (value, regel 12), `signalMatchesScopeSelector` (function, regel 31), `isRoutableSignalSnapshot` (function, regel 40), `partitionSignalSnapshots` (function, regel 46), `selectRoutableSignalBatchSnapshots` (function, regel 54), `quarantineUnsupportedSignalSnapshots` (function, regel 106), `createSignalBatch` (function, regel 159), `validSignalBatch` (function, regel 168), `acknowledgedSignalIds` (function, regel 187), `removeAcknowledgedSignalSnapshots` (function, regel 207).
- Directe consumers: `src/mind/agent.js`, `src/mind/autonomy.js`, `src/mind/tools.js`, `src/mind/understanding.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/mind/social.js`

- Omvang: 302 regels, 17987 bytes. SHA-256 `6d93a7a2f7bb9bd916029725a1a4ff2961647f16b8c7471cb14c98fb4703e590`.
- Invoer uit lokale modules: `src/db.js`, `src/mind/tools.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `RELATIONSHIP_STYLE_OUTCOME_CONTRACT` (value, regel 22), `selfMemoryFacets` (function, regel 70), `relationshipLessonFromText` (function, regel 99), `relationshipAppreciationFromText` (function, regel 135), `relationshipCharacterPreferenceFromText` (function, regel 161), `relationshipFeedbackFromText` (function, regel 179), `relationshipOutcomeFeedbackKind` (function, regel 191), `selfContinuityContextText` (function, regel 232).
- Directe consumers: `src/mind/agent.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: geen marker.

### `src/mind/telegramdelivery.js`

- Omvang: 318 regels, 13573 bytes. SHA-256 `b25b998aa8123f451d74d691f29a296206fc4d7edc6a63ae4a22b307da1f311e`.
- Invoer uit lokale modules: `src/db.js`, `src/util.js`, `src/mind/household.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `createTelegramDeliveryRegistry` (function, regel 212).
- Directe consumers: `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest `turn_tool_executions`; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `sqlite`.

### `src/mind/toolinstaller.js`

- Omvang: 767 regels, 32649 bytes. SHA-256 `268e90eb8b56229a0290b73b90855192ae15db3903de248581931330280dcc8f`.
- Invoer uit lokale modules: `src/db.js`, `src/config.js`.
- Externe invoer/dependencies: `node:fs`, `node:path`, `node:crypto`, `node:child_process`.
- Uitvoer/API: `inspectToolPackage` (value, regel 761), `discoverToolPackage` (value, regel 762), `installToolPackage` (value, regel 763), `listToolPackages` (value, regel 764), `toolPackageStatus` (value, regel 765), `activateToolPackage` (value, regel 766).
- Directe consumers: `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema `eva_tool_install_events`, `eva_tool_installations`; leest `eva_tool_install_events`, `eva_tool_installations`; schrijft `eva_tool_install_events`, `eva_tool_installations`; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `filesystem`, `network`, `process-spawn`, `sqlite`, `timer`.

### `src/mind/tools.js`

- Omvang: 5447 regels, 284414 bytes. SHA-256 `774d9c4e0615693f985c2415220686ac8a8ca4a6010d2df22575ad2564bf80eb`.
- Invoer uit lokale modules: `src/config.js`, `src/security/redaction.js`, `src/util.js`, `src/mind/learning.js`, `src/db.js`, `src/integrations/homeassistant.js`, `src/brain/llm.js`, `src/mind/household.js`, `src/mind/telegramdelivery.js`, `src/mind/work.js`, `src/mind/capabilities.js`, `src/integrations/robot.js`, `src/integrations/agentmail.js`, `src/mind/curiosity.js`, `src/mind/turnplan.js`, `src/mind/monitoringcontract.js`, `src/mind/communication.js`, `src/mind/economichandoff.js`, `src/mind/portfolio.js`, `src/mind/understanding.js`, `src/mind/signals.js`, `src/mind/workcells.js`, `src/mind/toolinstaller.js`, `src/mind/runtimecontroller.js`, `src/mind/home.js`, `src/mind/workspacefilemanifest.js`, `src/mind/workspacegeneration.js`, `src/mind/vision.js`, `src/mind/vision.js`, `src/mind/vision.js`, `src/mind/vision.js`, `src/mind/vision.js`.
- Externe invoer/dependencies: `node:fs`, `node:path`, `node:buffer`, `node:child_process`, `node:crypto`.
- Uitvoer/API: `revalidateWorkspaceFileManifest` (re-export, regel 166), `validWorkspaceFileManifest` (re-export, regel 166), `TOOL_EFFECT` (value, regel 200), `registerMessenger` (value, regel 238), `registerPhotoSender` (value, regel 239), `registerVoiceSender` (value, regel 240), `sendRegisteredMessage` (value, regel 241), `sendRegisteredPhoto` (value, regel 242), `sendRegisteredVoice` (value, regel 243), `validateToolRegistry` (function, regel 1326), `bindCapability` (function, regel 1409), `bindCapabilityInput` (function, regel 1414), `planCapabilityAction` (function, regel 1457), `bindExplorationTerminalContributionExecution` (function, regel 1510), `resolveIssuedSelfAttentionGoalPlan` (function, regel 1522), `bindHomeContextRecoveryPlan` (function, regel 1583), `priorVerifiedHomeContinuationResourceReceipt` (function, regel 1757), `bindPriorVerifiedHomeContextPlan` (function, regel 1782), `bindContinuityContextPlan` (function, regel 1812), `bindSignalAssessmentPlan` (function, regel 1833), `executionHandlesForPlan` (function, regel 1888), `executionObligationForPlan` (function, regel 1979), `executionGroupConsumptions` (function, regel 2108), `bindExecutionHandle` (function, regel 2239), `isIssuedExecutionProofReadback` (function, regel 2314), `resolveCapability` (function, regel 2328), `TOOL_REGISTRY` (value, regel 2351), `capabilityCatalog` (function, regel 2793), `TOOLS` (value, regel 2812), `toolPolicy` (function, regel 2814), `toolHasTag` (function, regel 2817), `toolAvailableInAutonomy` (function, regel 2818), `toolVerification` (function, regel 2821), `toolProofRank` (function, regel 2823), `toolSyntheticAllowed` (function, regel 2824), `toolIsDurable` (function, regel 2825), `toolRequiresEffectReport` (function, regel 2826), `toolEffect` (function, regel 2827), `toolAllowedByPolicy` (function, regel 2841), `toolChangesByDefault` (function, regel 2859), `toolsForTurn` (function, regel 2887), `executeTool` (function, regel 5385), `makeExecutor` (function, regel 5431), `__toolsTest` (value, regel 5437).
- Directe consumers: `src/integrations/telegram.js`, `src/mind/agent.js`, `src/mind/perception.js`, `src/mind/perceptionexecutor.js`, `src/mind/social.js`, `src/reminders.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest `cognitive_episodes`, `eva_episode_origins`, `eva_explorations`, `eva_work_items`, `timeline`; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: `LANG`, `LC_ALL`, `PATH`, `TZ`.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `environment`, `external-effect-call`, `filesystem`, `process-spawn`, `sqlite`, `timer`.

### `src/mind/turnplan.js`

- Omvang: 611 regels, 37584 bytes. SHA-256 `8f9c0b1ed41c14b7d832db41b8a953f58bd205e1a1ef6e0614394ddd4605c12d`.
- Invoer uit lokale modules: geen.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `priorActionResultFollowup` (function, regel 22), `localProjectReadRequest` (function, regel 44), `maskAdditiveNegation` (function, regel 53), `effectBoundary` (function, regel 179), `monitoringRequestSpec` (function, regel 494), `projectMonitoringStartArgs` (function, regel 511), `monitoringGuardValid` (function, regel 555), `turnToolGuard` (function, regel 594).
- Directe consumers: `src/mind/agent.js`, `src/mind/communication.js`, `src/mind/tools.js`, `src/mind/understanding.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: geen marker.

### `src/mind/turnunderstandingoutcome.js`

- Omvang: 181 regels, 7824 bytes. SHA-256 `f5808ed15b1f052b58b917b09c903ee6a6d83ebe9ec58ddb5d121a164dc3a8f5`.
- Invoer uit lokale modules: `src/util.js`, `src/mind/understanding.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `projectTurnUnderstandingTerminalOutcome` (function, regel 168).
- Directe consumers: `src/mind/agent.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/mind/understanding.js`

- Omvang: 4470 regels, 249999 bytes. SHA-256 `3128083bd4f3cb511d5bd7105438db5e0ef280b0478f5141b365062b7c52de42`.
- Invoer uit lokale modules: `src/db.js`, `src/util.js`, `src/mind/turnplan.js`, `src/mind/runtimecontroller.js`, `src/mind/monitoringcontract.js`, `src/mind/signals.js`, `src/mind/selfattentioncontext.js`, `src/mind/learning.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `CONTINUATION_DIALOGUE_ACTS` (value, regel 46), `iterativeExecutionBudget` (function, regel 180), `createTurnEnvelope` (function, regel 419), `fileArgumentsMetadataValid` (function, regel 462), `normalizeFileArgumentsMetadata` (function, regel 493), `semanticFailureResultMetadata` (function, regel 509), `retainedTurnResultMetadata` (function, regel 523), `PREPARED_UNDERSTANDING_TURN_CONTRACT` (value, regel 538), `createPreparedUnderstandingTurn` (function, regel 550), `verifyPreparedUnderstandingTurn` (function, regel 579), `projectContinuityWorkStart` (function, regel 1810), `verifyTurnUnderstanding` (function, regel 2729), `turnUnderstandingSemanticAuthoritySha256` (function, regel 2743), `isIssuedTurnUnderstanding` (function, regel 2759), `consumeIssuedAspirationExplorationAnchor` (function, regel 2762), `bindIssuedExplorationTerminalContribution` (function, regel 2785), `consumeIssuedExplorationTerminalContribution` (function, regel 2800), `reserveIssuedExplorationTerminalMutation` (function, regel 2859), `issuedExplorationTerminalMutationMatches` (function, regel 2868), `commitIssuedExplorationTerminalMutation` (function, regel 2873), `releaseIssuedExplorationTerminalMutation` (function, regel 2878), `bindIssuedSelfAttentionGoalAction` (function, regel 2883), `consumeIssuedSelfAttentionGoalAction` (function, regel 2904), `signalBatchForUnderstanding` (function, regel 2927), `continuityWatchForUnderstanding` (function, regel 2930), `createTurnUnderstandingRecord` (function, regel 2933), `verifyTurnUnderstandingRecord` (function, regel 3006), `understandTurn` (function, regel 3433), `decideFreeAutonomyContinuation` (function, regel 3765), `consumeIssuedFreeAutonomyContinuation` (function, regel 3854), `hasUnresolvedReference` (function, regel 3928), `decideTurnStep` (function, regel 4098), `createMonitoringContract` (function, regel 4252), `normalizeMonitoringContract` (function, regel 4317), `decideMonitoringOutcome` (function, regel 4363), `runMonitoringCycle` (function, regel 4427).
- Directe consumers: `src/mind/agent.js`, `src/mind/curiosity.js`, `src/mind/portfolio.js`, `src/mind/tools.js`, `src/mind/turnunderstandingoutcome.js`, `src/web/httpingress.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest `eva_explorations`; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `sqlite`.

### `src/mind/vision.js`

- Omvang: 513 regels, 21974 bytes. SHA-256 `50c5bf492c7473a19f9bf37a6973d3fbe5e0cc723cbc479d75ce0825d7ce72d2`.
- Invoer uit lokale modules: `src/db.js`, `src/config.js`, `src/brain/llm.js`, `src/integrations/homeassistant.js`, `src/mind/facerec.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `describeImageBuffer` (function, regel 44), `rememberNameablePhoto` (function, regel 85), `nameablePhotoForTelegramMessage` (function, regel 132), `createFaceIdentityClaim` (function, regel 268), `validateFaceIdentityClaim` (function, regel 303), `knownAppearances` (function, regel 322), `parseCameraSceneDescription` (function, regel 353), `lookAndDescribe` (function, regel 363), `nameLastSeen` (function, regel 422).
- Directe consumers: `src/integrations/telegram.js`, `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema `nameable_photo_messages`, `nameable_photos`; leest `eva_capabilities`, `memories`, `nameable_photo_messages`, `nameable_photos`, `people`, `sqlite_master`; schrijft `memories`, `nameable_photo_messages`, `nameable_photos`.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `sqlite`.

### `src/mind/voicebridge.js`

- Omvang: 893 regels, 36641 bytes. SHA-256 `5b074be5c1dc898f5a6acc5c06a00fcde64724b3a17e7222a8513bb2e1305d3f`.
- Invoer uit lokale modules: `src/db.js`, `src/util.js`, `src/voiceauthority.js`, `src/integrations/homeassistant.js`, `src/mind/portfolio.js`, `src/mind/agent.js`, `src/mind/agent.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `voiceLaneKey` (function, regel 50), `voiceRequestProvenance` (function, regel 61), `voiceReplyPayload` (function, regel 74), `normalizeVoiceConversationId` (function, regel 307), `voiceRequestInput` (function, regel 317), `verifiedVoiceUnderstandingSource` (function, regel 367), `verifiedVoiceDeliveryAuthority` (function, regel 405), `consumeVoiceDeliveryAuthority` (function, regel 418), `startVoiceBridge` (function, regel 439), `acceptAuthenticatedHaVoiceReplyAck` (function, regel 605), `acceptAuthenticatedHaVoiceQuery` (function, regel 703).
- Directe consumers: `src/index.js`, `src/integrations/homeassistant.js`, `src/mind/agent.js`, `src/mind/voiceingress.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `timer`.

### `src/mind/voiceingress.js`

- Omvang: 4 regels, 201 bytes. SHA-256 `1764f39d80b278b073dfd607c8eb7807015e3794d1b4146127bb0afdd53d4b16`.
- Invoer uit lokale modules: `src/mind/voicebridge.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `verifiedVoiceUnderstandingSource` (re-export, regel 3).
- Directe consumers: geen.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: geen marker.

### `src/mind/work.js`

- Omvang: 343 regels, 12842 bytes. SHA-256 `4f0080c43a75413335d610fdbb09f46b8f4adf0ea86d4165a8ac837da16c44f4`.
- Invoer uit lokale modules: `src/db.js`, `src/bus.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `terminalWorkProjection` (function, regel 70), `createWorkItem` (function, regel 102), `updateWorkItem` (function, regel 185), `listWorkItems` (function, regel 262), `claimWorkItem` (function, regel 291), `releaseWorkLease` (function, regel 310), `recoverWorkLeases` (function, regel 319), `durableWorkStatus` (function, regel 329).
- Directe consumers: `src/mind/agent.js`, `src/mind/autonomy.js`, `src/mind/portfolio.js`, `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema `eva_work_items`; leest `eva_goal_episodes`, `eva_work_items`, `sqlite_master`; schrijft `eva_work_items`; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert `work_changed`.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `sqlite`.

### `src/mind/workcells.js`

- Omvang: 572 regels, 20560 bytes. SHA-256 `3636870210b77d20682f5708ed88448d55feb29043573def1c5f41b2035c4d4e`.
- Invoer uit lokale modules: `src/db.js`, `src/brain/llm.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `createWorkCell` (function, regel 410), `listWorkCells` (function, regel 437), `getWorkCell` (function, regel 464), `runWorkCell` (function, regel 533), `resumeWorkCell` (function, regel 545), `WORKCELL_LIMITS` (value, regel 567).
- Directe consumers: `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema `eva_work_cell_runs`, `eva_work_cell_streams`, `eva_work_cells`; leest `eva_work_cell_runs`, `eva_work_cell_streams`, `eva_work_cells`; schrijft `eva_work_cell_runs`, `eva_work_cell_streams`, `eva_work_cells`; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `sqlite`.

### `src/mind/workcompletion.js`

- Omvang: 2438 regels, 109086 bytes. SHA-256 `145dc48825bba1f75cb4a6829c228c7c0d77cff81460f9354e7d9ae52e6d93a6`.
- Invoer uit lokale modules: `src/executionledger.js`, `src/turnauthority.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `WORK_COMPLETION_CONTRACT_VERSION` (value, regel 8), `createWorkCompletionPlanSeal` (function, regel 195), `inspectWorkCompletionHostSchema` (function, regel 749), `createWorkCompletionStore` (function, regel 1869).
- Directe consumers: `src/mind/materiallifecycle.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest `eva_episode_events`, `eva_episode_origins`, `eva_work_completion_claims_v3`, `eva_work_completion_contracts_v3`, `eva_work_completion_execution_bindings_v3`, `eva_work_completion_execution_observations_v3`, `eva_work_completion_proof_ready_v3`, `eva_work_completion_receipts_v3`, `eva_work_completion_settlements_v3`, `messages`, `sqlite_master`, `timeline`, `turn_tool_executions`; schrijft `eva_work_completion_execution_observations_v3`; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/mind/workspacefilemanifest.js`

- Omvang: 304 regels, 13277 bytes. SHA-256 `76a8a70dbbb2839a874f397b5a23f7111d4142d1602887bc7e796b519aa95629`.
- Invoer uit lokale modules: `src/config.js`, `src/security/redaction.js`, `src/util.js`.
- Externe invoer/dependencies: `node:fs`, `node:path`, `node:crypto`.
- Uitvoer/API: `WORKSPACE_FILE_MANIFEST_LIMITS` (value, regel 8), `WORKSPACE_FILE_MANIFEST_IGNORED_DIRECTORIES` (value, regel 16), `workspaceManifestSearchTerms` (function, regel 50), `validWorkspaceFileManifest` (function, regel 91), `createWorkspaceManifestState` (function, regel 130), `markWorkspaceManifestAmbiguous` (function, regel 140), `markWorkspaceManifestTruncated` (function, regel 143), `observeWorkspaceManifestEntry` (function, regel 154), `addWorkspaceManifestFile` (function, regel 180), `finishWorkspaceFileManifest` (function, regel 193), `revalidateWorkspaceFileManifest` (function, regel 292).
- Directe consumers: `src/mind/materialcompletion.js`, `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `filesystem`.

### `src/mind/workspacegeneration.js`

- Omvang: 1246 regels, 59042 bytes. SHA-256 `2b2e2050eba9e647a081cf7e15fa28259c7b8b6176888233a568f1fef0993dc3`.
- Invoer uit lokale modules: `src/config.js`.
- Externe invoer/dependencies: `node:fs`, `node:path`, `node:crypto`, `node:util`.
- Uitvoer/API: `WORKSPACE_GENERATION_STATE_CONTRACT` (value, regel 7), `WORKSPACE_GENERATION_CLAIM_CONTRACT` (value, regel 8), `WORKSPACE_GENERATION_OUTCOME_CONTRACT` (value, regel 9), `WORKSPACE_GENERATION_OBSERVATION_CONTRACT` (value, regel 10), `OS_EXCLUSIVE_WRITER_UNAVAILABLE` (value, regel 11), `createWorkspaceGenerationStore` (function, regel 1079), `workspaceGenerationContracts` (value, regel 1234), `__workspaceGenerationTest` (value, regel 1243).
- Directe consumers: `src/mind/tools.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest `sqlite_master`; schrijft geen; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `filesystem`.

### `src/reminders.js`

- Omvang: 103 regels, 3164 bytes. SHA-256 `40dfd2afde5b6fd9767b08945fc3f3e12f26dc4ae44db6478b829f245a7ea892`.
- Invoer uit lokale modules: `src/db.js`, `src/config.js`, `src/util.js`, `src/integrations/homeassistant.js`, `src/mind/tools.js`, `src/mind/persona.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `deliverReminder` (function, regel 24), `startReminderLoop` (function, regel 98).
- Directe consumers: `src/index.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `external-effect-call`, `timer`.

### `src/runtimeversion.js`

- Omvang: 39 regels, 1483 bytes. SHA-256 `8034ea113ade2cb364cc9db7fb689632b2172f545fef450573a482ad8cc673b8`.
- Invoer uit lokale modules: `src/config.js`, `deploy/eva-runtime-repair-protocol.mjs`.
- Externe invoer/dependencies: `node:path`.
- Uitvoer/API: `runtimeCodeSha256` (function, regel 7), `runtimeVersion` (function, regel 18).
- Directe consumers: `src/index.js`, `src/mind/runtimecontroller.js`, `src/web/httpingress.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: geen marker.

### `src/security/redaction.js`

- Omvang: 47 regels, 1918 bytes. SHA-256 `4c09d048e4eb22aafe79d1dd38affd327d7c5026be5a0f1600d213bd82b63f77`.
- Invoer uit lokale modules: `src/config.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `registerSensitiveValues` (function, regel 9), `redactSensitiveText` (function, regel 26), `redactSecrets` (function, regel 39).
- Directe consumers: `src/db.js`, `src/integrations/telegram.js`, `src/mind/agent.js`, `src/mind/curiosity.js`, `src/mind/learning.js`, `src/mind/perceptionattention.js`, `src/mind/persona.js`, `src/mind/portfolio.js`, `src/mind/selfattentioncontext.js`, `src/mind/tools.js`, `src/mind/workspacefilemanifest.js`, `src/self/vault.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: geen marker.

### `src/self/secretmind.js`

- Omvang: 65 regels, 2843 bytes. SHA-256 `b7e67f29bfa38a566b10faf7b70722b0af28ed73f1de605a6a513980f82f46e5`.
- Invoer uit lokale modules: `src/config.js`.
- Externe invoer/dependencies: `node:crypto`, `node:fs`, `node:path`.
- Uitvoer/API: `sealThought` (function, regel 43), `openThought` (function, regel 53).
- Directe consumers: `src/self/vault.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `cryptography`, `filesystem`.

### `src/self/vault.js`

- Omvang: 69 regels, 2276 bytes. SHA-256 `69caf83b2232981f32f486170bcb7e16936a562737910433da27dc8b3fdc449e`.
- Invoer uit lokale modules: `src/db.js`, `src/security/redaction.js`, `src/self/secretmind.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `migrateLegacySecrets` (function, regel 32), `setSecret` (function, regel 42), `getSecret` (function, regel 50), `listSecretKeys` (function, regel 56), `registerVaultSecretsForRedaction` (function, regel 61).
- Directe consumers: `src/index.js`, `src/integrations/agentmail.js`, `src/integrations/homeassistant.js`, `src/mind/capabilities.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: geen marker.

### `src/turnauthority.js`

- Omvang: 586 regels, 26420 bytes. SHA-256 `8a092cf94a9d18af684f941f4f13b1b16a85cc25949b1ee80f13452529e59c62`.
- Invoer uit lokale modules: `src/executionledger.js`, `src/util.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `TURN_UNDERSTANDING_AUTHORITY_CONTRACT` (value, regel 10), `TURN_UNDERSTANDING_AUTHORITY_TABLE` (value, regel 12), `TURN_UNDERSTANDING_AUTHORITY_TABLE_SQL` (value, regel 29), `TURN_UNDERSTANDING_AUTHORITY_TRIGGER_SQL` (value, regel 76), `turnUnderstandingAuthorityStoreStatus` (function, regel 377), `requireTurnUnderstandingAuthorityStore` (function, regel 406), `initializeTurnUnderstandingAuthorityStore` (function, regel 416), `turnUnderstandingAuthorityBody` (function, regel 498), `turnUnderstandingAuthorityRecord` (function, regel 525).
- Directe consumers: `src/db.js`, `src/integrations/telegram.js`, `src/mind/agent.js`, `src/mind/materiallifecycle.js`, `src/mind/portfolio.js`, `src/mind/workcompletion.js`, `src/web/httpingress.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest `sqlite_master`, `sqlite_temp_master`, `turn_tool_executions`; schrijft geen; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/util.js`

- Omvang: 437 regels, 22642 bytes. SHA-256 `948bcdac971be7981d77027f0686187bcd48e3c1eba21176ccb829f90cb96ddd`.
- Invoer uit lokale modules: geen.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `isoNow` (value, regel 1), `clamp` (value, regel 3), `normalizeUserFacingText` (function, regel 11), `normalizeDutchText` (function, regel 42), `dutchContentWords` (function, regel 54), `canonicalJson` (function, regel 60), `jsonSchemaViolation` (function, regel 77), `tekstOverlap` (function, regel 117), `parseHHMM` (function, regel 135), `isNight` (function, regel 141), `fmtDutch` (function, regel 152), `errMsg` (value, regel 159), `verifiedCompleteReceipt` (function, regel 164), `compactActionResultData` (function, regel 233), `classifyActionFailure` (function, regel 269), `normalizeActionFailure` (function, regel 281), `normalizeActionResultState` (function, regel 295), `boundedRuntimeMs` (function, regel 326), `auditActionResultOutput` (function, regel 333), `executionActionResult` (function, regel 362), `artifactReceiptChainMatches` (function, regel 404).
- Directe consumers: `src/brain/llm.js`, `src/db.js`, `src/integrations/homeassistant.js`, `src/integrations/telegram.js`, `src/mind/agent.js`, `src/mind/artifacts.js`, `src/mind/autonomy.js`, `src/mind/cognitive.js`, `src/mind/communication.js`, `src/mind/curiosity.js`, `src/mind/economichandoff.js`, `src/mind/emotions.js`, `src/mind/home.js`, `src/mind/learning.js`, `src/mind/materialcompletion.js`, `src/mind/materiallifecycle.js`, `src/mind/perceptionattention.js`, `src/mind/persona.js`, `src/mind/portfolio.js`, `src/mind/projectexecution.js`, `src/mind/signals.js`, `src/mind/telegramdelivery.js`, `src/mind/tools.js`, `src/mind/turnunderstandingoutcome.js`, `src/mind/understanding.js`, `src/mind/voicebridge.js`, `src/mind/workspacefilemanifest.js`, `src/reminders.js`, `src/turnauthority.js`, `src/voiceauthority.js`, `src/web/httpack.js`, `src/web/httpdelivery.js`, `src/web/httpingress.js`, `src/web/httpresponseauthority.js`, `src/web/server.js`, `src/web/sqliteindexattestation.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`.

### `src/voiceauthority.js`

- Omvang: 1113 regels, 52709 bytes. SHA-256 `df51fad15ca156292f3309cd1f458713c44ffc8003297aebe33c3fec6a74830e`.
- Invoer uit lokale modules: `src/util.js`, `src/integrations/homeassistant.js`, `src/integrations/homeassistant.js`, `src/mind/agent.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `VOICE_DELIVERY_AUTHORITY_CONTRACT` (value, regel 5), `VOICE_DELIVERY_AUTHORITY_TABLE` (value, regel 6), `VOICE_DELIVERY_AUTHORITY_TABLE_SQL` (value, regel 22), `VOICE_DELIVERY_AUTHORITY_INDEX_SQL` (value, regel 142), `VOICE_DELIVERY_AUTHORITY_TRIGGER_SQL` (value, regel 154), `VOICE_DELIVERY_AUTHORITY_PREDECESSOR_TABLE_SQL` (value, regel 235), `VOICE_DELIVERY_AUTHORITY_PREDECESSOR_TRIGGER_SQL` (value, regel 248), `VOICE_DELIVERY_AUTHORITY_PREDECESSOR_INDEX_SQL` (value, regel 269), `voiceDeliveryAuthorityStoreStatus` (function, regel 399), `initializeVoiceDeliveryAuthorityStore` (function, regel 445), `verifiedVoiceStoreEventSource` (function, regel 541), `consumeVoiceStoreEventSource` (function, regel 550), `recordVoiceQueryAuthority` (function, regel 793), `recordVoiceOfferIntent` (function, regel 823), `recordVoiceAckReceipt` (function, regel 831), `recordVoiceDeliveredProjection` (function, regel 844), `voiceDeliveryAuthorityRecord` (function, regel 852), `pendingAcknowledgedVoiceDeliveryPage` (function, regel 969), `pendingAcknowledgedVoiceDeliveries` (function, regel 1009), `verifiedAcknowledgedVoiceFinalDeliveryRecovery` (function, regel 1043), `verifiedVoiceFinalDeliveryRecoveryProof` (function, regel 1081), `consumeVoiceFinalDeliveryRecoveryProof` (function, regel 1106).
- Directe consumers: `src/db.js`, `src/mind/agent.js`, `src/mind/portfolio.js`, `src/mind/voicebridge.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest `sqlite_master`, `sqlite_temp_master`; schrijft geen; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/web/access.js`

- Omvang: 56 regels, 1989 bytes. SHA-256 `4c015f147998d952fa08569261bbec1b722c64fcaa9e0184f88b5add5d759193`.
- Invoer uit lokale modules: geen.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `isLoopbackHost` (function, regel 3), `assertSafeWebBinding` (function, regel 8), `requestAccessToken` (function, regel 15), `requestAuthorized` (function, regel 26), `normalizeWebConversationId` (function, regel 36), `familyTimelineEntry` (function, regel 48).
- Directe consumers: `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `cryptography`.

### `src/web/httpack.js`

- Omvang: 187 regels, 8142 bytes. SHA-256 `b1998958a4a8932a2b39860b48cfe730bc6f1f2ff9655bec5faa51b52682d145`.
- Invoer uit lokale modules: `src/util.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `canonicalHttpWireSha256` (function, regel 17), `normalizeHttpClientAckPublicKey` (function, regel 59), `canonicalHttpClientAckPreimage` (function, regel 90), `canonicalLegacyHttpClientAckPreimage` (function, regel 113), `verifyLegacyHttpClientAck` (function, regel 128), `verifyHttpClientAck` (function, regel 156).
- Directe consumers: `src/web/httpdelivery.js`, `src/web/httpingress.js`, `src/web/httpresponseauthority.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/web/httpdelivery.js`

- Omvang: 1844 regels, 84745 bytes. SHA-256 `daa8a04df5428d357964dec6b643afa04f3d0d2d74fe00c9769dd6a06155a518`.
- Invoer uit lokale modules: `src/db.js`, `src/util.js`, `src/web/httpack.js`, `src/web/httpresponseauthority.js`, `src/web/sqliteindexattestation.js`, `src/web/httpack.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `canonicalHttpClientAckPreimage` (re-export, regel 17), `verifyHttpClientAck` (re-export, regel 17), `httpDeliveryStoreStatus` (function, regel 1469), `beginHttpDelivery` (function, regel 1477), `replayHttpDelivery` (function, regel 1518), `claimHttpDeliveryWrite` (function, regel 1529), `claimHttpDeliveryTransmission` (function, regel 1594), `finishHttpDeliveryWrite` (function, regel 1672), `finishHttpDeliveryTransmission` (function, regel 1689), `abandonHttpDeliveryWrite` (function, regel 1709), `abandonHttpDeliveryTransmission` (function, regel 1725), `httpDeliveryWriteAuthorityCurrent` (function, regel 1741), `httpDeliveryTransmissionAuthorityCurrent` (function, regel 1748), `acknowledgeHttpDelivery` (function, regel 1764).
- Directe consumers: `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema `eva_http_delivery_ack_receipts_v3`, `eva_http_delivery_attempt_events_v3`, `eva_http_delivery_items_v3`, `eva_http_delivery_transmission_events_v3`; leest `eva_http_delivery_ack_receipts_v3`, `eva_http_delivery_attempt_events_v3`, `eva_http_delivery_items_v3`, `eva_http_delivery_transmission_events_v3`, `eva_http_response_authorities_v3`, `sqlite_master`, `sqlite_temp_master`; schrijft geen; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/web/httpingress.js`

- Omvang: 2440 regels, 107760 bytes. SHA-256 `be360bbfa5d99216e003074d64f1d1a8438c039018bdb036423121265ff0ffe0`.
- Invoer uit lokale modules: `src/db.js`, `src/executionledger.js`, `src/runtimeversion.js`, `src/turnauthority.js`, `src/mind/understanding.js`, `src/util.js`, `src/web/httpack.js`, `src/web/requestidentity.js`, `src/web/sqliteindexattestation.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `httpIngressStoreStatus` (function, regel 924), `canonicalHttpIntentSha256` (function, regel 997), `issueVerifiedHttpResultAuthority` (function, regel 1136), `durableHttpIngressResponseBoundaryStatus` (function, regel 1986), `consumeDurableHttpIngressResponseAuthority` (function, regel 1994), `httpIngressAuthorityCurrent` (function, regel 2063), `verifiedHttpIngressExecutionAuthority` (function, regel 2073), `renewHttpIngressAuthority` (function, regel 2096), `releaseHttpIngressAuthority` (function, regel 2117), `beginHttpIngress` (function, regel 2137), `prepareHttpResponse` (function, regel 2266), `settleHttpIngress` (function, regel 2393).
- Directe consumers: `src/mind/agent.js`, `src/web/httpresponseauthority.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema `eva_http_client_ack_bindings_v3`, `eva_http_ingress_claim_events`, `eva_http_ingress_requests`, `eva_http_ingress_state_events`, `eva_http_prepared_responses`; leest `eva_http_client_ack_bindings_v3`, `eva_http_ingress_claim_events`, `eva_http_ingress_requests`, `eva_http_ingress_state_events`, `eva_http_prepared_responses`, `sqlite_master`, `sqlite_temp_master`, `turn_tool_executions`; schrijft geen; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/web/httpresponseauthority.js`

- Omvang: 653 regels, 30506 bytes. SHA-256 `c383e2b15c82e27b6baf107b7eec289dc407c74f1350d60747820074717b938f`.
- Invoer uit lokale modules: `src/db.js`, `src/util.js`, `src/web/httpack.js`, `src/web/httpingress.js`, `src/web/sqliteindexattestation.js`.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `httpResponseAuthorityStoreStatus` (function, regel 576), `issueHttpResponseAuthority` (function, regel 584), `consumeHttpResponseAuthority` (function, regel 625), `httpResponseAuthorityCurrent` (function, regel 642).
- Directe consumers: `src/web/httpdelivery.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema `eva_http_response_authorities_v3`; leest `eva_http_client_ack_bindings_v3`, `eva_http_ingress_requests`, `eva_http_ingress_state_events`, `eva_http_prepared_responses`, `eva_http_response_authorities_v3`, `sqlite_master`, `sqlite_temp_master`; schrijft geen; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/web/public/app.js`

- Omvang: 944 regels, 38804 bytes. SHA-256 `ae76bb7ff828cdeafe07e218da36704a63e163ca3fe530c84455878ac271408f`.
- Invoer uit lokale modules: `src/web/public/httpackclient.js`, `src/web/public/autonomycard.js`.
- Externe invoer/dependencies: `three`, `three/addons/loaders/GLTFLoader.js`.
- Uitvoer/API: geen exports; side-effect- of entrymodule.
- Directe consumers: geen.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `network`, `timer`.

### `src/web/public/autonomycard.js`

- Omvang: 87 regels, 4127 bytes. SHA-256 `2338418ce945850f1988600e8e9759d64f93760bcdd0e8b1dd229b112b4cfb5c`.
- Invoer uit lokale modules: geen.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `autonomyCardCopy` (function, regel 39), `createLoadWork` (function, regel 57), `renderAutonomyCard` (function, regel 77).
- Directe consumers: `src/web/public/app.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`.

### `src/web/public/httpackclient.js`

- Omvang: 974 regels, 40454 bytes. SHA-256 `7fe77f96ae0b1b76bf198881b48210a8f143397c59a06fabc80b6eb03dd6fa9e`.
- Invoer uit lokale modules: geen.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `runHttpChatSubmitSingleFlight` (function, regel 22), `clientAckPublicKey` (function, regel 439), `storePendingHttpRequest` (function, regel 451), `storePendingHttpDelivery` (function, regel 506), `loadPendingHttpDelivery` (function, regel 557), `clearPendingHttpDelivery` (function, regel 713), `clearPendingHttpDeliveryOrReload` (function, regel 779), `retryPendingHttpDelivery` (function, regel 806), `discardPendingHttpRequest` (function, regel 880), `signHttpResponseAck` (function, regel 924).
- Directe consumers: `src/web/public/app.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`.

### `src/web/requestidentity.js`

- Omvang: 108 regels, 3624 bytes. SHA-256 `f8e981bfef284aedd9b25d9ebbcc8bb59c6babdac53e71abf8eec2c2580571e2`.
- Invoer uit lokale modules: geen.
- Externe invoer/dependencies: `node:crypto`.
- Uitvoer/API: `PUBLIC_REQUEST_ID_PATTERN` (value, regel 3), `PUBLIC_REQUEST_REPLAY_SCOPE` (value, regel 4), `canonicalPublicRequestId` (function, regel 18), `executeHostBoundPublicRequest` (function, regel 67).
- Directe consumers: `src/web/httpingress.js`, `src/web/server.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`.

### `src/web/server.js`

- Omvang: 1275 regels, 50283 bytes. SHA-256 `8aad39ddee35dd118a5ea5db1dddced26fd5dbde2501492f5a3675b6e420d49b`.
- Invoer uit lokale modules: `src/config.js`, `src/runtimeversion.js`, `src/bus.js`, `src/db.js`, `src/util.js`, `src/mind/agent.js`, `src/mind/understanding.js`, `src/mind/emotions.js`, `src/brain/llm.js`, `src/brain/oauth.js`, `src/integrations/homeassistant.js`, `src/mind/appearance.js`, `src/mind/autonomy.js`, `src/mind/capabilities.js`, `src/mind/learning.js`, `src/mind/perceptionattention.js`, `src/mind/voicebridge.js`, `src/web/access.js`, `src/web/requestidentity.js`, `src/web/httpingress.js`, `src/web/httpdelivery.js`, `src/web/httpresponseauthority.js`, `src/web/access.js`.
- Externe invoer/dependencies: `node:crypto`, `node:path`, `node:url`, `express`.
- Uitvoer/API: `publicBusEventData` (function, regel 100), `familyTimelineEntry` (re-export, regel 110), `startWeb` (function, regel 743), `default` (default, regel 1274).
- Directe consumers: `src/index.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest `eva_work_items`, `memories`, `messages`, `reminders`; schrijft geen.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: `GET /api/appearance`, `GET /api/capabilities`, `GET /api/emotions/history`, `GET /api/events`, `GET /api/health`, `GET /api/innerlife`, `GET /api/memories`, `GET /api/memories/overview`, `GET /api/memories/page`, `GET /api/overview`, `GET /api/people`, `GET /api/proposals`, `GET /api/reminders`, `GET /api/timeline`, `GET /api/workstatus`, `POST /api/agreement/remove`, `POST /api/auth/login`, `POST /api/chat`, `POST /api/http-delivery/ack`, `POST /api/memories/archive`, `POST /api/memories/restore`, `POST /api/memories/review`, `POST /api/voice`.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: `authority-or-proof-boundary`, `cryptography`, `sqlite`, `timer`.

### `src/web/sqliteindexattestation.js`

- Omvang: 56 regels, 2120 bytes. SHA-256 `dbb83aaf7b49d78ff8297075c16417116948fa5cac3ab0b0153da74ec9a4547b`.
- Invoer uit lokale modules: `src/util.js`.
- Externe invoer/dependencies: geen statisch zichtbare externe import.
- Uitvoer/API: `exactSqliteIndexSignatures` (function, regel 33).
- Directe consumers: `src/web/httpdelivery.js`, `src/web/httpingress.js`, `src/web/httpresponseauthority.js`.
- Statisch zichtbare SQLite-identifiers: schema geen; leest geen; schrijft geen; bevat daarnaast dynamische, hier niet benoemde SQL-identifiers.
- Statisch letterlijke bus-events: ontvangt geen detectie; emitteert geen detectie.
- Statisch letterlijke HTTP-routes: geen detectie.
- Directe process.env-properties: geen detectie.
- Heuristisch gedetecteerde bijwerkingen/grenzen: geen marker.

