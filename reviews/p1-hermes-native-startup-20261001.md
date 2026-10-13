# P1.1 Hermes startup and login-driver review — 1 October 2026

Independent bounded review against base `1d490929` of `p1_hermes_startup_probe.py`, `p1_hermes_login.py`, sanitized startup evidence and the two retained startup reports. Relevant pinned native constructor and authentication/store functions were inspected. No authentication, inference, tests or fixture replay were performed; credential contents and private configuration/challenges were not read. Only this review file was written.

**GO for the startup preparation and native login driver.** No actionable blocker was found in this scope. This does not confirm native access, the native loop, comparison admission, P1.1 completion or superiority.

The separate environment is recorded as an installation from the existing frozen lock. Both retained reports identify the expected source commit, lock hash and primary package versions. They show native Sol/none and Astra/medium constructor settings, `openai-codex`, `codex_responses` and the intended backend URL. An explicit inert key bypasses authenticated provider resolution; empty tools and skipped context files, memory and background review further limit the check. The evidence states those restrictions, missing authentication and zero inference, and correctly treats durations as startup time rather than answer latency.

The drivers clear inherited provider/config environment variables and select the independent custom Hermes home, with external login adoption explicitly disabled. For that custom-root layout, native global-root fallback resolves to the same Hermes home. Read-only Codex credential resolution suppresses adoption, refresh and normal auth-store writes. The login driver obtains a fresh native device-flow grant, saves it through native `_save_codex_tokens` with the default `write_through=False`, and then uses read-only resolution. It does not copy a rotating token from another application's store or return tokens to the comparison runner. The native save path creates credential JSON with mode 0600 under a protected parent; the native device flow prints the public URL/code, while the driver's status output contains only fixed metadata and sanitized exception type/code.

The login driver's safety and interface wiring were reviewed, but a successful login, same-account access or actual SDK request is not established by these constructor reports. The evidence leaves independent native login and provider/helper/tool/accounting/stop admission open. Unknown development credits remain unknown, and no Eva runtime change is claimed.

Source/report provenance matches the published hashes:

- Native commit: `f97608f178d1ffeca59860195ab7da295f7c8e5f`.
- Native lock: `5b3798f326209475abca8ef7cbf7c9406f12e687c28c0b540dfe597466f48590`.
- Startup probe: `655fb40d54da3b5f7f19ba529d8edcd76ea8228aa146ca3b2d26eac254556455`.
- Login driver: `d201bd5b610d1d69d055945192e366dd3adf11c449a52f12452697f47b8876ac`.
- Contact report: `c944429f482e3cb827f3b21de875d587471da723bf58a30cdb71c92fce6d673b`.
- Work report: `7b20f5f78544c4dbc2bdce319d2a8226f711b63eb7d3a02e26735df07d101044`.
