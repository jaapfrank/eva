# Telegram recovery delivered

Two actual Telegram errors were independently correlated: the core rejected a model answer beyond its identity word limit, and the bridge aborted another valid request at20seconds while the core later completed it. The reviewed fixes are now installed: corebc6d64a3 with exactly one source overlay on2393, and Bottie bridge89a5be01 via one explicit eva.service restart.

Long generated replies now return an explicit incomplete bounded answer rather than a stream failure; authoritative action result guards remain intact. Bridge deadlines distinguish20second connection,120second idle and300second total processing, reset idle on progress, preserve cancellation and close failed streams. No automatic duplicate request remains. The44second case, true timeout/cancel, late response and stream cleanup are covered offline.

Actual core source, readiness, bridge source/new stable process, exact12protected services, gateway guards, budgets/mandate, document journal and both old/completed AU006 goals were verified. No rollback, model test, outgoing Telegram message or replay of a user instruction occurred. An empty invalid live canary request reached422 validation; health/readiness returned200. No new startup stream/fatal failure observed. A real post-install Telegram user reply has not yet been observed, so delivery acceptance remains open.

Core source commit aa4a0bef; bridge source commit c133ca0d in the separate Bottie checkout. The live Bottie file was installed without staging or altering its Git index. Administrative integration must account for those already installed exact bytes. AU004 remains paused; its previous baseimage/seals must not be reused. Full hash-bound receipts and actual commands are in the linked JSON evidence.
