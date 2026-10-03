# Optional installer browser — independent installed review, 3 October 2026

**Installed GO for the optional browser outcome.** In the retained fresh installation, Eva completed an ordinary chat request to open a public page, follow its link and report the observed destination. The result is supported by durable tool receipts, a separate snapshot-only readback and the actual installed source. O1.2 as a whole remains open for its other selected modules, including voice; this review completes only the browser outcome.

The ordinary installer route was `http://127.0.0.1:44727/eva/`, namespace `eva-install-c32443b9475e`, original attempt `d5d35875-4421-438b-b7cd-72bb03061b1d`. The request asked Eva to use her own browser, open `https://example.com/`, click the link for more information and report the destination title and address. It did not require personal Chrome access, a shared tab or a browser-control button.

## Decisive result

Filtered the existing tool proof by the exact final message `dashboard:0e4e0f6f-a04a-421b-8565-482729be5326`. It contains four separately executed actions, each accepted and completed once:

1. `browser_navigate` opened `https://example.com/`.
2. `browser_snapshot` observed the “Learn more” link as `f2e13`.
3. `browser_click` used that observed reference and returned `https://www.iana.org/help/example-domains`, title “Example Domains”.
4. A further `browser_snapshot` observed that same destination.

Eva's final reply reported the same title and URL. The separately collected snapshot-only gateway readback also shows the IANA page and its “Example Domains” heading. The generic invoke receipts correctly retain `external_state_verified:false`; the subsequent observations provide the destination evidence instead of treating an accepted click as independent proof.

The final ordinary request completed in 36.036 seconds, HTTP 200, with no repetition of that request: five model responses, 20,390 input tokens, 260 output tokens and four requested tool calls. The existing combined event export also retains the two earlier failed model tasks. Across all three, the recorded counts are 13 responses, 46,525 input tokens, 789 output tokens and 10 model-requested calls. The implementer separately records seven diagnostic/readback browser calls and zero diagnostic model calls. These are runtime figures, not Codex development consumption; credits and monetary cost remain unknown.

## Installed binding and repair limits

Independently inspected the target core `e812eb846d421919c26b831fe0313188aa9bc849665d8f5c8b3175837e9beac6`: it was running and healthy on `sha256:e2d6af7ca0cba49761efe54c082ee1fab988f9de06680c01bfaa4fb6469a21d4`. Every actual mount matched the existing image-update receipt. Read the three actual source files under `/opt/eva/src/eva`; all match the prior source GO:

| File | SHA256 |
| --- | --- |
| `providers/mcp_gateway.py` | `09a655e9836f67bd5be61fdb9198f09e43d4d6253520862728cf6d6ab186f6dd` |
| `providers/mcp_discovery.py` | `2235114a301fdb0ada6a920ad8b3cb0bc17e6b496d7eab13b5f775e4cd3f2ac6` |
| `adapters/connectors/browser_autostart.py` | `ee873278392f2c2bfab67fc4c899699de480a106b5b7ea82ebc1a8d6b8717327` |

The update receipt is restricted to this fresh target's core. The main core still runs its separate L3 image `sha256:730524883c9c47c193b33e9a875ad4c4e3f3b722d315c50387fd10c8fdd59661`; the browser overlay did not replace it.

The retained native-process proof records the actually mapped Pixelflux extension as SHA256 `7d4fd0db93e48fd4e83eed1b4f6407f914f646d08c7a562b485d6d32ab89f281`, with `mapped_deleted:false`. This resolves the prior distinction between corrected disk bytes and an old compositor still loaded in memory. The successful ordinary task followed the targeted controller/native-process restart. Its evidence is distinct from the earlier direct diagnostic click.

This was assisted development recovery: earlier preparation/start failures, file permissions, the pinned wheel, owner-approved gateway egress, the reviewed direct-provider session fix and native-process restart were handled by operators. The original browser/profile, initialized authority and retained failure evidence were preserved. The earlier missing-session and stale-loaded-compositor tasks remain recorded as failures. This is not proof of an unattended fresh installation without those repairs.

The approved gateway attachment genuinely expands its network boundary, as documented in the [source review](installer-optional-browser-20261002.md); private binding and SSRF checks are not a container egress firewall. This installed result does not establish universal website/login/CAPTCHA compatibility, host reboot recovery or family acceptance. Existing owner-control/recovery constraints remain part of the reviewed implementation; they were not replayed as new acceptance tests here.

## Evidence

The implementer's [public outcome](../evidence/product/optional-browser-installed-20261003.json), inspected at SHA256 `bc04e9aefee18a457c69ef5b9abd433a50051ca95d7a3f76f7ce997f47c88371`, indexes the retained private evidence. Decisive inspected artifacts under `/home/administrator/project/.eva2-artifacts/installer-optional-browser-20261002` are:

- `final-browser-tool-proof.private.json`: `d7a28fe10041e0be1b6b1ec93016c191fbaa9178ec4aaf8aa26db879161208e2`.
- `final-independent-browser-readback.private.json`: `5a537c2ec7c047fec066775f9cd7d10e99e7f9c7540903a04eb8bbe7e4a80fe1`.
- `pixelflux-loaded-proof.json`: `9d6e436582c13649ced2a56de8fa04a59b2a3e3f31175e4f19042ba8f7fc3f19`.
- `direct-browser-session-fix/update.private.json`: `9dbdc80a99fac9b4de7e11b8527ab681d2523a12fca99d33961dc2904a46d428`.

No model calls, browser actions, test reruns, deployment changes or product-source edits were performed by this final review. No additional test is needed to accept this bounded browser outcome.
