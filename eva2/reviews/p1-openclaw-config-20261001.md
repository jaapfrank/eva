# P1.1 OpenClaw native request preparation review — 1 October 2026

**GO for publishing this bounded preparation evidence.** No concrete blocking defect was found. This does not admit OpenClaw, validate persistent startup configuration, or complete P1.1 or an Eva product function.

The reviewer inspected `p1_openclaw_config_probe.mjs`, the sanitized evidence, OpenClaw nativeConfiguration manifest entry, retained stdout/stderr, and the relevant installed native builder, resolver and harness callsites. No fixture replay, authentication, inference or native-source change was performed. Only this review file was written.

The installed `@openclaw/codex` package is 2026.9.6. Its npm lock resolved URL and integrity match the evidence; the locally resolved OpenClaw package also reports 2026.9.6. Independently recomputed hashes match the retained record:

- Probe: `7a452aa8924865a07e8877fa8a684cff65d0a77b11c25c727be5596b032e402e`.
- Native package.json: `487c9e30a10e22b167be27884eba958e5204296ae799caa16138090e489a5315`.
- Native builder: `d278e65190cb7e9a9187962fd413187bdd09e5e431454e9287f29ab15f6d9bce`.
- Retained stdout: `af412da2463a6bb2ee5fc43fee7758e421c3b611f425fd755ed82e2f715d4967`.

These identify the inspected inputs; they are not an independent source-to-build provenance verification.

`thread-lifecycle-B3mfz1TG.mjs:2675` constructs the native turn request, with collaboration effort resolved at lines 2754–2764 and copied to the top-level effort field. The real harness calls this builder at `run-attempt-BHu11r7h.mjs:8016`, then sends the constructed request at line 8114. The probe invokes the builder directly, not that complete route. Its bare declared model names return directly from the native model-selection helper; no authenticated model selection is demonstrated.

The retained Astra fixture has explicit medium at both effort fields and the requested model at both model fields. The Sol off fixture has null at both effort fields, so its explicit-none match correctly remains false. The native resolver at thread-lifecycle lines 54–75 reads `params.model.compat.supportedReasoningEfforts`; its off branch returns none only when that metadata includes none, otherwise null. The fixture supplies no such metadata. This source branch was inspected, not exercised through a real model catalog. Null does not establish the provider default, latency, model rejection, or inability to support none.

The fixture config is declared input alongside explicit model/think params. The probe bypasses outer-host validation and selection. This GO expressly does not endorse its codex/ model prefix as valid persistent startup; the updated evidence retains the limitation against the bundled canonical openai/ host references. Neither displayed config nor `harness.supports` proves model catalog or authentication admission. The latter is a native capability check, not a provider request.

The probe requires its explicit isolated HOME, CODEX_HOME and state paths, refuses the two standard auth files, supplies an empty auth-profile store, and installs fetch/socket/child-process guards before native imports. Existence-only review checks found neither standard auth file in the fixture profile. Retained stderr is empty, and stdout records zero guarded network and child-process attempts. The isolated network namespace and clean subprocess environment are coordinator execution provenance; stdout alone does not independently attest every environment field. No credential contents were read during review.

Evidence and manifest preserve not_admitted, missing auth/catalog, unproved persisted host selection, full native loop/helper behavior, provider acceptance, tool/data parity, accounting and stop coverage. There is no measured reply, quality, latency, competitor win or runtime saving. The scoped null-versus-none finding is correctly retained without altering native source, injecting catalog support or substituting models.
