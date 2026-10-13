# Onderzoek bestaand compleet gesprekssysteem — 10 september 2026

## Uitkomst en harde grens

**Geen van de onderzochte applicaties is als volledig passende oplossing bewezen.** LiveKit Agents en Pipecat zijn actief onderhouden en leveren veel benodigde mechanismen. Dat maakt hun starters nog geen complete Eva-gespreksoplossing. Er is geen keuze voor implementatie of uitrol gemaakt. Dit is een begrensde onderzoeksuitkomst, geen bewijs dat nergens een geschikt systeem bestaat.

Jaaps selectie-eis blijft: alles moet al samen in een onderhouden, uitvoerbaar gesprekssysteem zitten. De gebruikerssituatie is een echte taak of onderzoek opgeven, tijdens uitvoering doorpraten, relevante voortgang horen, met gewone spraak corrigeren of onderbreken, en het echte resultaat terugkrijgen. Stoppen met spreken en stoppen met werken moeten afzonderlijk werken. Context mag onuitgesproken tekst niet als gehoord behandelen. Geen ingeblikte opvulzinnen als voortgang.

Telefoon/laptop is het eerste toegangspunt. Home Assistant en Voice PE bepalen de keuze niet. Een koppeling met Eva's bestaande geheugen, taakuitvoering, identiteit en rechten is toegestane integratie; zelf ontbrekende gespreksmechanismen ontwerpen valt buiten dit onderzoek. De bestaande Eva-kern blijft leidend.

## Onderhouden kandidaten en geleverde applicaties

Versies en activiteit zijn op 10 september via GitHub gecontroleerd; de ruwe metadata en exacte commits staan in het [verificatiebewijs](../../evidence/product/voice-oss-verification-2026-09-10.json).

| Kandidaat | Onderhoud / licentie | Bestaand resultaat | Waarom niet volledig gekwalificeerd |
|---|---|---|---|
| LiveKit Agents | [1.8.0, 5 september](https://github.com/livekit/agents/releases/tag/livekit-agents%401.8.0); hoofdbranch 10 september; Apache-2.0 | Geïntegreerde spraaksessie; officiële Python- en React-starters, beide bijgewerkt 4 september, MIT | Python-starter heeft alleen een uitgecommentarieerde weerfunctie. Werkelijke lange taken, bijbehorende voortgang en correcties zijn niet aangesloten. |
| Pipecat | [1.8.1, 27 augustus](https://github.com/pipecat-ai/pipecat/releases/tag/v1.8.1); hoofdbranch 10 september; BSD-2-Clause | Uitvoerbare voorbeelden met async tools en een browserdemo voor achtergrondonderzoek | Asyncvoorbeeld gebruikt vertraagde fictieve resultaten en een vaste opvulzin. Onderzoeksdemo simuleert bronnen en toont voortgang/resultaten op het scherm; gesproken inhoudelijke opvolging ontbreekt daar. |
| pipecat-homeassistant | [0.1.81, 11 augustus](https://github.com/kyvaith/pipecat-homeassistant/releases/tag/v0.1.81); broncommit 8 augustus; MIT | Geïntegreerde addon met browserroute en configureerbare pipelines | Geen aangetoonde complete lange-taakgespreksproef. Klassieke Assist-bridge ondersteunt volgens de README geen echte barge-in. Browserroute verdient een afzonderlijke beoordeling als passend bewijs verschijnt. |
| TEN Framework | [Bron bijgewerkt 10 september](https://github.com/TEN-framework/ten-framework/tree/ed41bc1862ef83604c955910f3d29843b4d10001) | Startbare voice-assistant en voice-assistant-realtime met frontend | Voorbeelden bewijzen de complete taak-/correctiecyclus niet. Bovendien bevat de [licentie](https://github.com/TEN-framework/ten-framework/blob/ed41bc1862ef83604c955910f3d29843b4d10001/LICENSE) aanvullende beperkingen op eindgebruikersapparaten en concurrerende inzet; dit wordt niet als gewone Apache-2.0 aangeboden. |
| FastRTC | [0.0.34](https://github.com/gradio-app/fastrtc/releases/tag/0.0.34), 24 november 2025; laatste gevonden hoofdcommit 12 januari 2026; MIT | Compacte realtime audio/webbouwsteen | Geen recent onderhouden complete oplossing voor deze taakcyclus aangetoond. Dit is geen claim dat het project definitief verlaten is. |

[Linux Voice Assistant](https://github.com/OHF-Voice/linux-voice-assistant) is actief (1.1.15 van 2 augustus, hoofdcommit 3 september; Apache-2.0), maar is een huis-audio-ingang, geen vervanger voor de vereiste volledige gesprekssessie.

## Wat de twee sterkste motoren werkelijk al bevatten

**LiveKit:** [async tools](https://docs.livekit.io/agents/logic/tools/async/) kunnen via `RunContext.update` voortgang teruggeven terwijl nieuwe gespreksbeurten doorgaan. De functie bestaat in de gepubliceerde 1.8.0-bron. [Turn handling](https://docs.livekit.io/agents/logic/turns/) ondersteunt onderbreken en aanpassen van gespreksgeschiedenis aan afgespeelde spraak. Automatische spraakonderbreking vervangt geen expliciete opdracht om een echte achtergrondtaak te annuleren. De [officiële starterbron](https://github.com/livekit-examples/agent-starter-python/blob/6b63960b36ac0da64824b8c94a6baa1bf5862ea2/src/agent.py) laat zien welk werk nog niet aangesloten is.

**Pipecat:** [function calling](https://docs.pipecat.ai/pipecat/learn/function-calling) ondersteunt async uitvoering met `cancel_on_interruption=False`, tussentijdse updates en afzonderlijke annulering via `cancellable_by_llm`. Dit is aanwezig in 1.8.1. Het [asyncvoorbeeld](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/examples/function-calling/function-calling-openai-async.py) demonstreert meerdere mechanismen samen, maar de taken zijn gesimuleerd. De [onderzoeksdemo](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/examples/multi-worker/ui-worker/async-tasks/bot.py) heeft een browserclient, achtergrondwerk, schermvoortgang en annulering. Het model wordt expliciet naar het scherm verwezen voor voortgang. [Contextbeheer](https://docs.pipecat.ai/pipecat/learn/context-management) ondersteunt koppeling aan uitgezonden spraak; nauwkeurigheid hangt mede af van TTS-timestamps en transport.

Voor beide blijft fysieke echo-onderdrukking en Nederlandse barge-in op het gekozen apparaat onbeproefd. Browser/WebRTC biedt een passende ingang; dit bewijst niet dat de bestaande Voice PE-firmware geschikt is. LiveKit kan [zelf gehost](https://docs.livekit.io/transport/self-hosting/) worden, maar dat levert geen gratis model-/spraakdiensten op. De standaardstarters gebruiken diensten met eigen toegang en kosten; aanvullende audioverbetering heeft eveneens eigen voorwaarden. Pipecats lokale WebRTC-transport verplicht geen Daily-abonnement, maar gebruikte STT/LLM/TTS kunnen betaald zijn. Geen nieuwe accounts, diensten of betaalde calls zijn gestart.

## Uitgevoerde verificatie en resterende onzekerheid

In een geïsoleerde tijdelijke Python-omgeving is de gepubliceerde `livekit-agents==1.8.0` geïnstalleerd. Drie bestaande testmodules uit dezelfde release zijn ongewijzigd uitgevoerd: foreground/background run tracking, behoud van toolresultaten bij onderbreking, en markering van nog lopende tools. **19 tests slagen**, met twee waarschuwingen over een onbekende pytestmarker. Een eerdere verkeerd geordende CLI-optie selecteerde geen tests; het bewijs vermeldt ook die poging.

Dit zijn tests met upstream fake LLM/audio en virtuele tijd. Ze onderbouwen de sessiemechanismen, niet verstaanbaarheid, werkelijke echo, Nederlandse correcties, Eva-integratie of eigenaaracceptatie. Geen eigen prototype of nieuwe gesprekscode gebouwd.

Er zijn ook actuele foutmeldingen bekeken. [LiveKit #7198](https://github.com/livekit/agents/issues/7198) meldt een onderbrekingsrace met Deepgram Flux op 1.7.1; dat is geen bewezen fout in de hier geteste 1.8.0. [Pipecat #5611](https://github.com/pipecat-ai/pipecat/issues/5611) gaat over gebruiksmeting bij onderbreking en [#5647](https://github.com/pipecat-ai/pipecat/issues/5647) over pipelinebuffering. Deze meldingen onderstrepen de noodzaak van een echte proef; ze bewijzen niet dat ieder gesprek mislukt.

## De door Jaap genoemde openai_tts

[sfortis/openai_tts 3.9.1](https://github.com/sfortis/openai_tts/releases/tag/v3.9.1) is een onderhouden GPL-3.0 Home Assistant-integratie, uitgebracht 7 september. Zinsgewijs beginnen met spreken is sinds 3.9 een gepubliceerde optie, dus niet alleen ontwikkelbranchfunctionaliteit. Het is een afzonderlijke optie per profiel. Dit kan de spraakweergave helpen als tekst al in delen beschikbaar is.

Een read-only controle vindt de integratie niet geïnstalleerd of geconfigureerd op de huidige HA; de actieve route gebruikt HA Cloud TTS. Er is niets geïnstalleerd. De huidige Eva-route retourneert eerst één volledig JSON-antwoord. Alleen TTS vervangen geeft daarom geen gesprek tijdens werk, vroege Eva-tekststroom of gewone spraakonderbreking. Deze repository blijft een ondersteunende kandidaat.

## Concrete vervolgstap zonder de eis te verlagen

De onderzoeksoplevering is afgerond; de natuurlijke stemervaring is niet gebouwd of geaccepteerd. Pipecat heeft van de geïnspecteerde voorbeelden de breedste runnable demonstratie, LiveKit heeft sterk onderbouwde geïntegreerde sessiemechanismen. Geen van beide krijgt hier het label complete oplossing.

Voor een vervolg onder dezelfde harde eis is eerst een bestaande referentieapp nodig die echte taakuitvoering én inhoudelijke gesproken opvolging/correctie samen bevat. Pas na zo'n vondst: versie vastzetten, de ongewijzigde app op laptop/telefoon draaien met toegestaan providergebruik, vervolgens alleen Eva's bestaande taak- en contextkoppeling toevoegen. Geen eigen scheduler, onderbrekingsmachine of voortgangsmechanisme als ontbrekend puzzelstuk bouwen.

De fysieke proef blijft: een controleerbare taak opgeven; tijdens uitvoering een andere vraag stellen; het doel corrigeren; een gesproken antwoord onderbreken zonder de taak te verliezen; daarna de taak expliciet annuleren; controleren dat geen laat resultaat alsnog als actueel wordt uitgesproken. Meet eerste hoorbare antwoord en stoptijd, controleer context en taaklog, en laat Jaap de vloeiendheid beoordelen. Zonder die proef blijven functies gedocumenteerd/getest, maar niet door de eigenaar geaccepteerd.
