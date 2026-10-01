# P1.1 OpenClaw persistent main-role config review — 1 October 2026

**GO for the bounded configuration acceptance/readback checkpoint.** No concrete blocking finding was found. This does not prove native harness selection, authentication, supported effort none, provider acceptance, comparison admission, P1.1 completion or an Eva product function.

Review base: `a4524901`. The reviewer inspected the new probe, evidence, OpenClaw nativeHostConfiguration entry and retained result/contact/work files. Installed manifest and entry files were read only to recompute their recorded hashes. No CLI, model or authentication command was rerun; no extra executor or native-source change was made. Only this review file was written.

All independently recomputed bindings match the evidence:

- Probe: `6d48370321a0e3968436e4a8436a22f7b6093efd03f21ac53888f91b74dc8272`.
- Raw result: `6b53dcd2d10de9a091f28f4f735f0db4dbb34eadd79826983ad61f6019b3c9b5`.
- OpenClaw package manifest: `53e483cf761dfe5d773b1b1a3f26a07146c6d5d1f7c2ead25e8a2e422c4d3cbf`.
- Codex plugin manifest: `487c9e30a10e22b167be27884eba958e5204296ae799caa16138090e489a5315`.
- CLI entry: `4dd36d2ed0cb154456c4a09a951a2e8f2508a5dd6ff2fe0688d4e1c8f773fcb5`.
- Contact config: `b59a1b86fcaa20df5b16fe515fa64c9d5be9a4cc2fbde9ad8f9279a98adeffc0`.
- Work config: `77c3dfb2a122acf5e1908406d9600791c9645d526e39d03795c49470542f64e8`.

OpenClaw and its plugin both identify version 2026.9.6. Managed Codex 0.155.1 is reused from prior evidence and was not relaunched here. Hash identity is not independent source-to-build certification.

The retained argv records exactly config validate and config get agents.defaults for each role. All four commands exited 0 with empty stderr; both validation JSON replies report valid true, the intended config path and no warnings. Their recorded durations sum to 6.292 seconds. Current config bytes match both retained before and after hashes, confirming the authored files were preserved.

Both native readbacks exactly retain the authored agents.defaults fields: canonical openai/ primary model, empty fallbacks, workspace, role thinkingDefault and both model-scoped Codex runtime/thinking entries. Contact selects gpt-6-sol/off; work selects gpt-6-astra/medium at this configuration boundary. Whole-dict equality remains false because native readback adds maxConcurrent 72, subagent maxConcurrent 8/archiveAfterMinutes 60 and safeguard compaction. The evidence correctly preserves that mismatch. Those additions are observed defaults, not an approved comparison budget or evidence of helper execution. The CLI get covers agents.defaults; plugin fields remain in the unchanged validated files, but plugin activation and runtime routing were not read back or exercised.

The probe refuses profile reuse, creates private directories and supplies an explicit child environment with isolated HOME, CODEX_HOME, state/config paths, TMPDIR and readonly configuration. Reviewed directory modes are 0700 and config modes are 0600. Captured /proc/self/ns/net is net:[4026532785], distinct from the recorded and currently observed outside net:[4026531833]. Together with the recorded unshare invocation this supports the stated namespace provenance. The host-shared /sys/class/net list is not isolation proof, as the evidence explicitly acknowledges. Individual provider attempts remain uninstrumented.

Schema acceptance and persistent file readback do not establish an actual native agent turn, catalog entitlement, off-to-none mapping, helper selection, tool access, stop behavior, quality or latency. The participant remains not_admitted, and the contact model remains absent from the prior unauthenticated catalog. No substitute model, credential adoption, inference or benchmark quota change is endorsed. No Eva runtime installation changed; P1.1, the final comparison and owner acceptance remain open. Developer credits remain unknown.
