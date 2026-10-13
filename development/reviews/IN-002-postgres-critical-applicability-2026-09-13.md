# IN-002: remaining PG16 critical findings and license evidence

Bounded primary-source triage on official 16.15-bookworm. All 16 critical package findings (seven CVEs) remain in the original scan; no allowlist or suppression was added. The paired JSON maps each exact package/version/CVE to its primary source and assessment.

- **CVE-2026-13221**: Debian bookworm table says vulnerable, but notes introduce change in Perl 5.37.10 while installed is 5.36.0. Conflict unresolved: retain finding pending source/backport analysis. Trixie listed fixed 5.40.1-6+deb13u1.
- **CVE-2026-42496**: Bookworm postponed pending upstream regression resolution. No installed-package fixed version supplied. Upstream Archive::Tar fix referenced at 3.08; this is not a safe Debian package substitution.
- **CVE-2026-8376**: Debian bookworm no-dsa minor issue, not a security exemption. Advisory specifically concerns 32-bit Perl builds; exact candidate Perl reports ptrsize=8 and ivsize=8. Narrow architecture applicability evidence only, raw finding retained.
- **CVE-2025-7458**: Bookworm no-dsa minor issue, still vulnerable. Vulnerable code introduced SQLite 3.39.0; installed 3.40.1 is within that range. No installed-package fixed version supplied.
- **CVE-2026-6653**: Bookworm postponed; tracker says UAF arrives via CVE-2021-3541 backport, upstream fixed 2.11.0. Installed 2.9.14 remains unresolved. Libxml2 is in PostgreSQL runtime dependency tree; no reachability claim.
- **CVE-2023-45853**: Debian ignores bookworm source zlib because vulnerable contrib/minizip is not built into these binaries. This supports a package-specific applicability distinction for zlib1g, not an image-wide exception; raw finding retained.
- **CVE-2025-68121**: Official Go advisory concerns crypto/tls session resumption with mutated CA config. Fixed Go 1.24.13/1.25.7/1.26rc3. gosu contains Go1.24.6; whether affected TLS symbols are linked/reachable is not proved. Retain unresolved.

The retained Debian pages include current distribution statuses and upstream links. `no-dsa`, `postponed` and `ignored` are interpreted individually, never as a general declaration of safety. The Perl architecture probe ran against the exact candidate image with networking disabled. This is not an exploitability or whole-image clearance.

An explicit isolated scan compared official 16.15-trixie (`f1c3376c…`) against Bookworm using the same unchanged current Trivy database. Trixie gives 101 HIGH / 14 CRITICAL, versus 96 / 16: fewer critical findings but a higher combined count. It is **not selected**, and no OS switch is made. Current Debian fixes also do not imply that this particular published image includes them. Runtime/restore and license compatibility for the alternative have not been proved.

The 26 unknown declared-license nodes are now distinguished: 22 Debian packages have their actual image copyright text preserved, including two archive hardlinks resolved to GCC copyright; three Go modules have version-specific upstream license files; one node describes the aggregate image archive. A wrong initial Moby license URL returned 404 and is retained alongside the successful repository-root lookup. These concrete texts support subsequent license review; the SPDX document and its unknown declarations were not rewritten, and no legal release was manufactured.

Next bounded remediation: establish package-specific dispositions using the captured primary evidence and investigate a maintained gosu binary with fixed Go or absence of affected symbols. Remaining Perl/SQLite/libxml2 fixes require a reviewed image/package update rather than treating distro delay as approval. Any alternative OS route first requires the same synthetic upgrade/restore/rollback and license checks. Existing platform release blockers remain open.

Primary sources are linked per finding in the JSON: Debian Security Tracker and the official Go vulnerability database. Durable artifacts: `.eva2-artifacts/in002-platform-boundary-20260913/postgres-critical-triage`. Canonical pins, production services and support labels are unchanged.
