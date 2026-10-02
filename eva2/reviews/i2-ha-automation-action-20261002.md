# I2 — Home Assistant automation callback review

Status: GO for the bounded callback, installed and live checked on 2 October 2026.
Implementation: `completion_diagnosis`. Independent source/integration review:
Codex `/root`. Documentation: `ha_action_usage`. Base: `02bea1b2`.

## Concrete missing transition

The installed HA `conversation.process` schema cannot provide a satellite/device
binding, while Eva's existing conversation entity requires one. The general HA
API, source discovery, image/calendar access and automation writer already exist.
`eva2_voice.ask` exposes the existing device-bound client to those automations.
It adds no planner, private identity, implicit speech or example-specific rule.

## Review and correction

Verified permanent service registration, enabled registered satellite selection,
exactly one loaded client, existing household authority, response/field schema,
cancellation propagation and sanitized errors without retries. The handler checks
that the client/device binding is still current before returning.

The review found that a request could start during the awaited playback report.
The implementation now checks active requests both before and after that await.
Automation calls decline a busy client; ordinary voice supersession keeps its
existing default. The two race/busy cases are included in the focused tests.

## Evidence and limits

49 focused tests passed in 0.59s (existing HA entry and native client suites).
The four installed file hashes match the reviewed source. One HA core restart
loaded the action with optional response support. An actual HA action returned
`verbinding werkt.` through the existing Eva core in 7.681 seconds. No TTS,
camera/calendar request, recurring automation or household device action was
requested. Core and background worker remain healthy.

The synthetic source-selection run chose the garden camera and working alternative
calendar after an empty source. It is not live household observation evidence.
The separate real camera-description/shared-calendar provider trial was rejected
before execution by automatic approval review; the payload permission remains
pending. It was not retried through another route.

I2.1/I2.2 and family acceptance remain open. Installed callback availability is
not proof that Eva has created and run a useful recurring observation. No general
speed advantage or new complete product category is claimed. Full measurements,
source hashes and retained backout artifacts are in
`eva2/evidence/product/i2-ha-automation-action-installed-20261002.json`.
