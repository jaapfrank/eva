# P1.1 native access review — 30 September 2026

Independent static review of the private `native-auth-access-v1/driver.py` and the relevant native OAuth, credential-path and model-catalog functions at Agent Zero commit `e3051fb584b1a36be2b0a0c90606f1c2c2d356ec`. No authentication, inference or tests were run by the reviewer. Credential files, private challenges and authentication logs were not read. This review does not admit a live comparison or complete P1.1.

## Findings resolved after review

1. **P2 — public authentication failures sanitized.** The original driver copied `str(exc)` into the printed report while its local `auth` variable was still `None`. Native `_token_error_message` can return the complete upstream response body. The corrected handler publishes only the exception type, failure stage and a constant summary; it does not publish raw authentication exception text. This source correction was reviewed statically, without replaying authentication.
2. **P3 — direct-call counter scoped.** The original `authRequests` counter omitted native token exchanges and possible refresh requests. The corrected driver uses `nativeFunctionCalls` and leaves `nativeHTTPRequestCount` null. The scoped retained report explicitly labels the counter as direct device-login/model-catalog invocations, with internal HTTP requests unmeasured. It preserves the successful run as retained evidence rather than presenting a replay.

## Confirmed scope

The driver passes only an explicit private `auth_file_path` through the native configuration normalizer. The native path validator rejects known Codex credential stores and aliases, and the native resolver has no fallback credential import. New private directories use mode 0700 and native credential writes use mode 0600. Writable native lock and installation paths under `usr` are redirected to the private artifact directory.

The requested model names are checked against the actual native model catalog: the configuration supplies no model list, so `fetch_models` calls the native `GET /models` route. Device authorization, token exchanges, credential readback and that catalog call are access operations. The driver invokes no inference endpoint. Catalog visibility does not establish successful inference, supported reasoning settings, complete sample accounting or cancellation.

## Retained access result and disposition

The scoped report records successful native credential readback from a distinct owned store with mode 0600. The first native catalog supplement records six models for WSL client version 0.144.1, without `gpt-6-sol` or `gpt-6-astra`. A subsequent genuine catalog readback uses the supported native `codex_version` setting, bound to installed desktop CLI version 0.159.2, and lists ten models including both selected names. The configuration supplies an empty `models` list, so the catalog is not a configured-list override. Neither catalog absence nor presence establishes inference acceptance, and no inference request was made. The reviewer did not inspect the credential file or private challenge.

The credential-free OAuth template hash is `6cb70e4fe315fd7fa159f7b2171d41b7567908f9f73b985c7244ac1010e26f32`; it matches the participant manifest, public evidence and retained native save/load report. Native `plugins.save_plugin_config` and `get_plugin_config` use the normal configuration storage and lookup route. The retained network-disabled report records saved configuration and credential lookup, with no inference or external network requests. It does not establish a running OAuth proxy or installed Agent Zero deployment. The blocked cost-map lookup is explicitly retained as an attempted lookup with local fallback.

The public evidence and participant access record expose same-account parity only as a boolean. They retain the prior catalog observation, preserve unknown total HTTP usage and distinguish authenticated readback from live comparison admission. The missing pre-correction executed-driver hash is explicitly recorded as a provenance limitation; the current driver hash is not represented as the executed hash. The new admission checkpoint keeps full P1.1 completion open.

**GO for the corrected narrow access procedure, native configuration template and sanitized evidence boundaries.** Both review findings are closed; the final source/evidence scope check found no further blocker. The successful authentication and prior native cases were not replayed; raw executed evidence remains private. Live inference admission, complete sample accounting, cancellation/tool/sync coverage and P1.1 completion remain open.
