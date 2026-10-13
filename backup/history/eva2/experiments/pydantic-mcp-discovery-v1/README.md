# Pydantic AI native MCP discovery candidate

This isolated candidate asks one unchanged Pydantic AI agent to use tools from
two unrelated in-process MCP providers. The providers own their names, schemas,
descriptions and fake state. Eva's instructions contain no provider, product,
device or protocol names.

The real-model proof has four deliberately different cases:

- a present, implicit comfort need should cause one provider-owned change and
  a later independent observation;
- a historical observation must not cause a change;
- a general preference must not cause a change;
- a location-free current complaint must not cause a change and should lead to
  a clarifying question;
- an implicit focus-time request should use a second provider, then read the
  reservation back.

All effects are in-memory fixtures. The proof does not access the LAN, Home
Assistant, a calendar, files, a live database or any physical device. The
access-only Codex lease is mounted read-only only for the real model call and
is never written to output.

This is a dependency and behavior candidate, not a production integration.

## Result on 31 August 2026

The current PyPI releases used for the retained proof were Pydantic AI `2.36.0`
and FastMCP Slim `3.4.7`. All five real-model cases passed. A clean Python 3.12
environment installed the 82-distribution experiment lock with mandatory hashes,
then passed all five local tests and `pip check`.

The experiment lock deliberately contains the FastMCP server extra because the
fixtures publish tools. That is not the proposed Eva runtime surface. The
separate client-only measurement contained 69 distributions: 46 additions and
8 upgrades relative to the repository's current 38-distribution lock. This is
large enough that an image build, SBOM, scan and source-removal measurement are
mandatory before promotion. The behavior result alone does not justify adding
that dependency surface to the live core.

The client-only lock was then installed from 69 hashed wheels in a build with
networking disabled. The resulting non-root image is
`sha256:bebcd2cdfcbcf4ce77d719b104aa9c76f97d1fc548877caf18c85d52101411ca`.
It is 114,760,217 bytes, 33,090,981 bytes larger than the current Eva image.
Syft produced a CycloneDX SBOM and a fresh Trivy database from 31 August found
zero HIGH and zero CRITICAL findings.

The exact image also connected over an internal-only Docker network to a
separate MCP fixture, discovered its provider-owned tool and called it. The
client image did not need FastMCP's server extra. A fifth local test proves that
Pydantic AI's public `process_tool_call` callback can intercept a selected MCP
tool before any provider I/O. That is the required seam for keeping Eva's
durable ledger and readback boundary while Pydantic AI owns discovery and the
model loop.

This closes the image and architectural-fit checks, but not promotion. The
candidate still adds 46 distributions and 33 MB. It may enter Eva only in a
replacement diff that removes the overlapping hand-written model/MCP loop;
running both loops beside each other is a failed result.
