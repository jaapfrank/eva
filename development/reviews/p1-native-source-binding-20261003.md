# P1 native source binding — independent review, 3 October 2026

Decision: **GO for the actual Bottie source binding and explicitly declared disposable equal-model variant only.** No blocking finding remains within this change. P1 remains open; this is neither complete sample admission nor a live comparison or quality result. The reviewer read source and existing receipts, verified hashes, and made no test, model or provider calls.

The source is bound to the observed `eva.service` working directory, `/home/administrator/project/Bottie`, at HEAD `909bff513d7d7fd653d6a7f270d9e73facce430d`. Its declared local changes remain visible. This is an on-disk source observation, not an attestation of code already loaded into process memory. The earlier fixture used the Eva2 repository copy; its offline evidence is retained without upgrading it to actual Eva1 runtime provenance.

All 86 original and variant files match the frozen manifest. The separately recorded original `deploy/eva-runtime-repair-protocol.mjs` matches both trees, giving 87 source/package/import files. Only `src/config.js` and `src/mind/agent.js` differ, exactly as the patch declares: `EVA_REASONING_REPLY` configures the composer while the default remains `low`. The actual Bottie source is unchanged. The existing native dependencies were physically copied after the native guard rejected a symlink; both dependency trees have recorded SHA-256 `31fe36a05acb7ada9e9b73c56bc7dd0f942e3fa4e4b701a67fafd138d9828582`. No package download or guard removal was used.

The optional absolute `--source-root` binds native imports to the selected tree and reports seven entry/source hashes before import. It keeps the default fixture path, private temporary data, and explicit exclusion of the source `.env`. The new test exercises two actual native foreground dispatches with synthetic `gpt-6-sol`/`none` responses through the existing parent ledger: 34 input and 10 output fixture tokens, HTTP success, independently read SQLite response identity/body hash, then local stop with zero active attempts or pending controls. These are controlled synthetic calls, not live model usage.

The history is preserved: the initial run had one failure and one default-path pass because a static deploy import was missing; the next failed-only run encountered the native dependency-symlink guard; the final failed-only case passed in 1.33 seconds. The retained default-path pass and earlier control evidence are reused. This is not represented as an initially clean run. The optional test's import/dependency corrections did not change native source, assertions, model roles or limits.

The manifest and admission text accurately separate stock native product from the disposable equal-model variant and leave admission false. Available individually admitted participants may later run under the existing owner instruction, without reallocating absent participants' budgets or claiming full EV-003 superiority. This change enables no such run. Buffered response observation remains unsuitable as latency-neutral streaming evidence; auxiliary-call coverage, live stop and full-sample admission remain unproved.

The existing installed-source Hindsight receipt supports the separate accounting obstacle: char/4 usage estimates, no demonstrated durable-operation-to-sample binding, and cancellation limited to pending operations. No memory content was inspected. This source binding does not repair or waive that obstacle.

Bound SHA-256 values:

| Input | SHA-256 |
| --- | --- |
| `cp001_bottie_worker.mjs` | `e577dfe56f7ae4e9ae9095a91991e6829eee024726e588ae623633c5b00977bc` |
| `test_cp001_native_bottie.py` | `a033d18e7d4c62f60ceabd571f3c4dc9a7d46dc1080644fa8535a7975dc8cb82` |
| `p1_bottie_equal_model.patch` | `e7473bd4f5ea80477b6ae8462cf3b390491d85266ebbb301f5a77bffefbb8646` |
| Private source `manifest.json` | `e49cad570953623465861bcca6b467560d214d0b937c7e280630f405d31811e4` |
| Private `source-dependencies.json` | `4e135c57180a408815ed36496be2062bc518e27a14f08369abe6e5a285f67e7a` |
| Private `dependency-copy.json` | `3d2559db188ec5dff0b6d3fe400b5123d1ac0da4ef8bd9baa172a6566a54bb0e` |
| Private final `native-result.json` | `8e7c4c3c9d9b9dfaccb8d59d84015c99b1b849a2c69fa2375adfb96926572ca4` |
| `p1_participants_20260929.json` at review | `dc1fe98af38b5da848baa8ed39f51a624bb975fed44ece1df893207709ecf946` |
| Public `p1-native-source-binding-20261003.json` at review | `e5babd89baf4d59658221560b2be7cb3202451c44042d28ab2c55a3159dc97d6` |

Private receipts are under `.eva2-artifacts/p1-bottie-native-source-20261003-v1`; the final receipt binds the exact source and prerequisite receipts. No additional tests are needed for this bounded review decision.
