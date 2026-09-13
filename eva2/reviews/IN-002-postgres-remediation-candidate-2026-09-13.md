# IN-002 PostgreSQL remediation candidate

Official PostgreSQL 16.15 Bookworm is selected as a bounded candidate, retaining the existing Debian release. Registry root `sha256:bb3e1a57e5407e0a5280b4211980a5e537f4abd234a87014ac979849a78dd825`; linux/amd64 manifest `sha256:1938c16e9d2f10a6a3623b344b64ae8d45f407f2c5f34f0979468bb689b9227a`. Official-images and index annotations agree on source commit `9d15534160ade17f2b6c455a39ee967c49b1937d`, directory `16/bookworm`. Observed 13 September 2026. This is a registry content-digest/source-provenance check, not an independent signature verification.

Using the exact unchanged Trivy database bytes from the preceding old-image scan, HIGH findings fall from 110 to 96 and CRITICAL from 18 to 16. Sixteen package findings disappear and none appear. Both critical GnuTLS findings disappear. These are package finding counts, not distinct CVE counts. The full remaining findings, fixed-version metadata and source URLs are in the paired JSON. There is no security-release approval or exploitability assertion.

The exact image has a fresh Syft SPDX inventory. Upstream image license and PostgreSQL license pages are retained alongside the pinned Dockerfile. Image metadata does not constitute legal clearance; individual package license unknowns remain subject to the existing inventory/review process. No canonical dependency pin, production service or platform support label changed.

The next bounded step is an isolated synthetic old-to-new backup/restore and rollback check in the already provisioned private guest. This candidate evidence does not claim that check has completed. The existing release process still requires that result, independent review, the unresolved vulnerabilities and license disposition, and the remaining platform contracts before any release decision.

Primary sources: https://github.com/docker-library/official-images/blob/master/library/postgres ; https://github.com/docker-library/postgres/tree/9d15534160ade17f2b6c455a39ee967c49b1937d/16/bookworm ; https://www.postgresql.org/about/licence/

Durable raw artifacts and hash bindings: `.eva2-artifacts/in002-platform-boundary-20260913/postgres-remediation`, paired evidence JSON. Old and new images are scanned offline using the same pinned existing scanner; no model calls or private data.
