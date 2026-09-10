# Owner phone panel candidate

The existing household conversation integration remains separate and enabled.
This candidate adds an optional HA admin panel that also requires the existing
Eva owner dashboard token. The household integration token never opens it.

Build: `python tools/build_ha_phone_panel.py /new/output/eva2_voice` materializes
all component files, the existing laptop UI as an HA web component, and verified
LiveKit client 2.22.3. It records each output hash in phone-build.json.

The selected SDK uses its supported dual peer connection mode so signaling stays
on /rtc with access_token. The HA capability is bound to the admitted admin and
issued room JWT scope. A refreshed JWT may change validity times; issuer,
participant, room, permissions and metadata must match. Standard kind/identity
defaults emitted by LiveKit are normalized. This proxy filter never authenticates
a JWT: the upstream LiveKit server still checks every signature. A real server
probe accepted its refreshed token and rejected a tampered signature. Full phone
reconnection remains an installation/physical trial.

The gateway is opt-in: render_household_voice_ingress.py --owner-voice adds only
fixed media/session and LiveKit signaling paths to the existing HA-IP-only port.
No new public listener or firewall widening is required by this design. The
existing household paths and slot cutover lifecycle remain intact. Without the
flag, the generated gateway is unchanged.

HA starts a leased media room. Renewals every two seconds require the same active
HA admin; the media server expires an unrenewed room after ten seconds. Closing is
bound to the panel capability and exact room. Pending cleanup survives caller
cancellation, and backend close failures are retried. Ordinary laptop rooms do
not require these renewals.

Status 2026-09-10: reviewed media with leased endpoints and Cloud relay is live;
the existing HA-IP-only gateway has owner routes enabled through the versioned
owner-phone-ingress.conf drop-in. A verified existing HA configuration deployment
route and installation of the prepared component bundle are still needed. Then reload the existing integration and enable
Eigenaarspaneel inschakelen in its options. A failed optional panel setup preserves
the existing household conversation platform. Disable removes the loaded panel,
closes its leases, and leaves the household agent usable.

Evidence so far: real HTTP and binary WebSocket transport, an actual nginx proxy
inside an isolated network namespace, cancellation/revocation/unload regressions,
room-bound close and heartbeat expiry, and a real browser UI login/remount trial.
The UI trial used synthetic readiness, not actual HA authentication. The existing Cloud relay now carries synthetic audio in an isolated test, and
the actual Windows browser completed speech recognition, an Eva core answer and
received audio on the live relay route. Installation in HA and physical phone
microphone/audio have not been proved. Do not describe this route as live or accepted.
