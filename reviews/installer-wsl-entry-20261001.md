# O1.1 WSL installer source/package checkpoint — 1 October 2026

**GO for this partial source/package checkpoint only.** No remaining concrete defect was found in the frozen candidate. This is not a supported platform, approved release, fresh installation, full O1.1/O1.2 delivery or owner acceptance.

Base: `d6f3b983`. The review read AGENTS and current owner decisions, the bounded contract/registry/preflight/generator/schema changes, candidate lock, launcher/stager, tests, developer packaging instructions and retained package/check receipts. The reviewer did not rerun tests, open authentication, launch a provider or Docker target, or modify implementation or workboard files. Only this review file was written.

Independently recomputed frozen source, package and staging-receipt hashes agree:

- Canonical entry: `82eb06f63747b332c6125e30d49c7b5ac0fb278a44d507b10c70b150a597173c`.
- Launcher: `1630ba69f9a78d8d320263d76b9fd866da5cfd687e120960d73288959a6e7e1d`.
- Stager: `e48f9bce8461c378df0a4c2f90347223adf346ec87aaec6db9bcf61c54b581e2`.
- WSL candidate lock: `545836dde8b73d087a2280e19b934a6b1006254a4db7bf74bf9ca488c6c639b0`.
- Package staging receipt: `fb0463939ae9db933eb6fdf6cdf7e3bd4693e7f57c3c4fa277076b278c321e25`.

All eleven evidence source hashes and all 1,199 staged source-file hashes match current source and package bytes. The receipt explicitly records source changes against the base and staged_not_validated; it is not a signed release approval.

PlatformLock/v4 adds the distinct first Ubuntu24.04.4 WSL2 amd64 target without changing the v1/v2/v3 source matrices or schema bytes. Registry dispatch is explicit. Preflight requires actual Ubuntu guest/version/architecture plus Microsoft WSL2 kernel metadata, preserves exact Python3.12.14 and Docker-version checks, and does not infer support from a display label or environment variable. The selected candidate remains unsupported/unvalidated with no invented receipt or runnable OCI image. Validated claims require the target receipt and resolved digest/SBOM-bound image state. The current support gate stays closed.

The generated v4 schema, registry/corpus dispatch and declared host-conformance layer are consistent with that boundary. The corpus adds one valid v4 case and removes none of its 160 prior cases. Historical PlatformLock schemas are byte-identical to the base. DelegatedWorkUsage and its embedded Result schema only align input/total maxima to unchanged source values 3,200,000/3,300,000; this changes generated metadata, not runtime budgets. The retained generator comparison confirms identical v4 schema under actual pinned Pydantic2.12.5 and the developer generator version. Host conformance remains required beyond standalone JSON Schema.

Package assembly selects Git-controlled regular inputs, rejects overlapping/existing outputs and linked source entries, and records the exact local Linux amd64 image. The source export uses an unstarted network-none container. Cleanup removes only the exact export container after checking its image, nonce label and created state; it does not force-remove unrelated or running containers. The two historical .playwright-mcp cache files are absent and the exclusion is explicit. The coordinator corrected only its own new package, retained the runtime export and reported no Eva service restart; this review did not inspect live Docker cleanup or perform target provisioning.

The launcher checks the bundled interpreter, requires Python3.12.14 before opening the wizard, replaces inherited Python path/home/user-start settings, disables user-site packages and selects packaged libraries. Retained runtime checks identify Python3.12.14/Pydantic2.12.5 with user site disabled. This isolates wizard dependency loading; it is not an operating-system sandbox or release-authority check.

The initial smoke had supplied its lock path explicitly. The final correction records the assembler-selected Git-controlled lock and makes the canonical entry verify its relative release path and retained byte hash before supplying the default. Explicit operator selection remains available; the legacy direct wizard/v3 default is unchanged. The final no-explicit-lock smoke matches the retained evidence: bootstrap and authenticated HTTP200, actual Python/WSL host/engine passed, release support blocked, no installation/provider operation, and its own smoke server stopped. This local content binding does not create trusted public release authority.

Verification is reused: 35 initial focused checks passed with the packaged interpreter; the final seven affected launcher checks passed in0.08s, with the unchanged30 preflight results retained. No broad or repeated suite was run by the reviewer.

The ordinary target-settings factory and approved signed release/trust inputs remain integration dependencies. The source trust-root slot is unusable, and prior synthetic test authority is not a production approval. The existing fresh starter uses an internal traffic-closed restore graph: healthy containers would not alone prove model access or an ordinary usable user route. No fresh database/runtime, full installation, platform support, physical acceptance or O1 completion is established here. Model/house calls remain zero in the retained checkpoint; development credit cost is unknown.
