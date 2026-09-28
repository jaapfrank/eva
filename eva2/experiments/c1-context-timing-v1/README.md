# Existing conversation timing attribution

Bounded read-only C1 diagnostic on source be0776b3. No model generation or user task is submitted.

- context.py reads current task/provider context under the configured conversation scope and separate delegated storage authority. Its prefix guard admits only SELECT/SET/SHOW and preserves existing shared binding locks. It is not a general SQL sandbox; the inspected methods perform reads. The statement count includes untimed construction.
- greeting-history.py reads the last five exact greetings and recorded model timings in the current owner's scope. Whole-second admission timestamps cannot establish exact HTTP latency.
- tokenizer-load.py measures local vocabulary initialization in an isolated process.

The product evidence also uses the already-saved result-turn trace and historical Hindsight logs. Private logs remain outside Git. Preliminary scope/timestamp mistakes are explicitly excluded. The discarded duplicate-anchor patch was not installed: it would not fix Hallo.

Do not rerun unchanged diagnostics or interpret their durations as a complete current channel benchmark. The ordinary installed-route trial remains open.
