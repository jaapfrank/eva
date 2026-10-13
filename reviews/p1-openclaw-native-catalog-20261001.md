# P1.1 OpenClaw managed native catalog review — 1 October 2026

**GO for publishing this scoped metadata checkpoint.** No concrete blocking finding remains. This is not authenticated model entitlement, OpenClaw host selection, comparison admission, P1.1 completion or an Eva product delivery.

The reviewer inspected the probe, current evidence and nativeCatalog manifest entry, retained result/stderr, and the installed managed launcher. No binary invocation, fixture replay, login, inference or native-source change was performed. Only this review file was written.

The existing managed package is `@openai/codex` 0.155.1. The retained version readback and native initialize user-agent both identify 0.155.1, with initialize confirming the isolated CODEX_HOME. Independently recomputed hashes match the evidence:

- Probe: `ad486923d1de1e3cd75c3541758c0574ba982020b2af4a95f263c99cd8d4af4e`.
- Package.json: `98862962c00eef34e3946f14a4345c26efbaaa733f8c6188069654a234adeee0`.
- Node launcher entry: `61b0194f3bb6534439c8d26a3ed57d0805f84b884588b761795323eeb92fcf70`.
- Linux native binary: `0753dfe1d8b87a52436deb13eb1c549661ef4c84fee2c5aa688385eebeccb761`.
- Retained result: `919ffe607d65a042c378513e7b0a87c34c35dded885098d3ad7439b9948942d0`.
- Retained stderr: `7f18e3a0149a699f54f2acfb1841564327196887558fd2f27d3b6ba86e319aa1`.

The actual client exchange contains only initialize, initialized, account/read with refreshToken false, and model/list with includeHidden true and limit 100. The probe whitelist excludes login, thread and turn methods. Account/read returns account null and requiresOpenaiAuth true. The successful model response contains nine entries with nextCursor null. Astra is listed with medium; the exact selected Sol identifier is absent. This is a complete returned catalog for this one fresh unauthenticated session, not proof of a complete authenticated entitlement catalog. The similarly named gpt-5.6-sol entry is not the selected gpt-6-sol.

The probe creates a fresh profile and CODEX_HOME rather than adopting an existing one, and supplies an explicit child environment containing only PATH, LANG, HOME, CODEX_HOME and TMPDIR. Existence-only inspection found no auth.json, and profile, CODEX_HOME and tmp modes are 0700. The retained launcher vector records unshare user/map-root-user/net. Namespace execution and tool exit 0 are coordinator execution provenance; individual native connection attempts were not instrumented. The evidence correctly leaves those attempts unknown instead of claiming zero.

Cleanup is appropriately bounded. The probe closes stdin, requests launcher termination and awaits it in finally. The installed launcher forwards termination to its native child and waits for that child exit. The retained metadata result is written before finally and does not capture the native exit code; current evidence explicitly records that limitation alongside the tool-reported exit 0. This supports the reviewed local cleanup route and reported completed run, not cloud cancellation, all-descendant quiescence or a model-stop result.

The bubblewrap warning announces a bundled fallback; no tool or sandbox execution was performed, so it proves no functioning sandbox. The catalog may likewise be unauthenticated bundled fallback. Neither Astra presence nor Sol absence establishes provider acceptance, authenticated rejection, account entitlement, persisted host configuration, latency or quality. The evidence and participant entry retain not_admitted and these boundaries. No source upgrade, alternate model, credential adoption or inference is justified by this checkpoint. P1.1 and the final comparison remain open; developer credits remain unknown.
