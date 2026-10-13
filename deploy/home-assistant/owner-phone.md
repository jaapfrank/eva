# Owner phone panel

The existing household conversation integration remains separate and enabled.
The installed option adds an HA admin panel that also requires the existing
Eva owner dashboard token. The household integration token never opens it.

Build: `python src/tools/build_ha_phone_panel.py /new/output/eva2_voice` materializes
all component files, the existing laptop UI as an HA web component, and verified
LiveKit client 2.22.3. It records each output hash in phone-build.json.

The selected SDK uses its supported dual peer connection mode so signaling stays
on /rtc with access_token. The HA capability is bound to the admitted admin and
issued room JWT scope. A refreshed JWT may change validity times; issuer,
participant, room, permissions and metadata must match. Standard kind/identity
defaults emitted by LiveKit are normalized. This proxy filter never authenticates
a JWT: the upstream LiveKit server still checks every signature. A real server
probe accepted its refreshed token and rejected a tampered signature. Physical phone
reconnection remains an owner trial.

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

Status 2026-09-12: the owner panel is installed and enabled in the existing HA integration.
A real browser completed synthetic speech input, core response and received audio
through the existing remote HTTPS/WSS and Cloud relay route. Physical phone
microphone, echo, interruption and owner acceptance remain separate.

The installed package preserves the previous household client.py and conversation.py
bytes; it adds only the reviewed owner panel and its lifecycle/configuration.
See voice-phone-installation-2026-09-12.json for the initial composite manifest;
voice-client-lifecycle-2026-09-12.json records the subsequent panel asset update.

The reviewed media with leased endpoints and Cloud relay is live;
the existing HA-IP-only gateway has owner routes enabled through the versioned
owner-phone-ingress.conf drop-in. The existing authenticated File editor and Terminal & SSH provide installation
and independent recovery. The component is installed at
/config/custom_components/eva2_voice in the SSH add-on. Stage and back up outside
custom_components, compare every hash, and preserve the existing household files.
An ordinary config-entry reload does not replace cached Python modules: a source
update needs one coordinated HA Core restart. Other services remain running.
After HA is RUNNING and the household entry is loaded, enable
Eigenaarspaneel inschakelen through the normal options flow. A failed optional panel setup preserves
the existing household conversation platform. Disable removes the loaded panel,
closes its leases, and leaves the household agent usable.

Evidence so far: real HTTP and binary WebSocket transport, an actual nginx proxy
inside an isolated network namespace, cancellation/revocation/unload regressions,
room-bound close and heartbeat expiry, and a real browser UI login/remount trial.
The earlier UI trial used synthetic readiness, not actual HA authentication.
The installed-panel trial on 12 September used a real HA-authenticated session. The existing Cloud relay now carries synthetic audio in an isolated test, and
the actual Windows browser completed speech recognition, an Eva core answer and
received audio on the live relay route. Installation, actual HA authentication, remote browser audio, disabling/re-enabling
and room-bound cleanup are now proved. Physical phone microphone/audio and owner
acceptance have not been proved.

## Lifecyclecorrectie 12 september 2026

Het paneel en de laptopclient stoppen eigen microfoontracks bij verbergen,
offline gaan, verbindingsherstel en afsluiten. Terugkeren opent de microfoon niet:
kies opnieuw **Begin gesprek**. Een verwijderde pagina wist de aanmelding.
Vertraagde verbindingen en microfoontoestemming ruimen alleen hun eigen room op.
Alleen audio wordt beëindigd; reeds toegelaten werk blijft bij de kern.
Bij tijdelijk mislukte afsluiting blijft de exacte room voor herstel bewaard;
de bestaande backend bewaakt daarnaast verlopen en lege rooms.

[Lifecyclebewijs](../../evidence/product/voice-client-lifecycle-2026-09-12.json)
onderscheidt de deterministische tests, browserproeven en live assetcontrole.
Fysieke telefoonmicrofoon, echo, schermvergrendeling, procesbeëindiging en
het luisteroordeel zijn hiermee niet geaccepteerd of bewezen.

Probeer: open een gesprek, verberg het paneel en keer terug. De microfoon hoort
uit te blijven totdat je opnieuw Begin gesprek kiest. Bij verwijderen van het
paneel moet opnieuw aanmelden nodig zijn. Werk dat al gestart was is geen
stopopdracht gegeven; controleer het resultaat via het bestaande dashboard.

## Phone result connection — 1 October 2026

The installed HA asset now handles the existing private media service's
`background_result_part` and `background_result_unavailable` messages. It joins
bounded parts, verifies the complete UTF-8 hash, and displays the saved result
as text. The shared client clears partial-result and unavailable caches at the
existing conversation-stop boundary; current-attempt checks still reject late
packets and pending digests from a previous visit.

Only `phone-assets/panel.js` and its composite `phone-build.json` were replaced.
The installed SDK, HA Python files, authentication, media service and core remain
unchanged. Two old companion hashes in the receipt were corrected to their
already installed source versions. Every installed file, exact backups, public
asset bytes and owner readiness were checked; no service restart was performed.
The panel SHA256 is
`d078f20f9d4ad51129f2f8cb08c1f23d4de7453952239d5872a4bebeedffda4c`.
The original two files remain at `/config/.eva2-phone-saved-result-20261001`;
recovery restores only those files, without changing household Python modules.

The real installed producer and generated UI passed the focused in-memory
transport check, including a fresh visit after end. Independent review approved
the exact candidate and installation route. These checks do not establish
physical phone microphone quality, echo or barge-in. C4.3 and C4 remain open.

To try the physical phone route:

1. Refresh the HA app's page and open **Praten met Eva** (`/eva-owner-voice`)
   with the existing owner sign-in.
2. Choose **Begin gesprek** and say “Vertel een kort verhaaltje.”
3. While Eva is speaking, say “Hoe gaat het met jou?” without a wakeword.
   The old speech should stop and the new question should receive its own answer.
4. **Eindig gesprek** closes audio. It is not an instruction to stop accepted
   background work; use an explicit task-stop request when that is intended.

[Delivery evidence](../../evidence/product/c4-phone-route-20261001.json) and
[independent review](../../reviews/c4-phone-saved-result-20261001.md) distinguish
the installed result receiver from the still-open physical phone trial.
