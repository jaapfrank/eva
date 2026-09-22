# Snelle gespreksroute — integratiekandidaat, 22 september 2026

De kandidaat voegt een optioneel contactmodel toe aan de bestaande
`LivingConversation`. Geen begroetingsregex, kanaalbrein, nieuwe provider of
dependency. De werkmodelroute blijft bestaan voor echte toolvervolgstappen en
autonoom initiatief. Identiteit, toegelaten geheugen, recente context, tools,
stopcheckpoints, antwoordguard, requestjournaal en budgetten worden gedeeld.

Na `start_delegated_work` of `resume_delegated_work` sluit de contactroute de
pollingtools voor deze beurt af. Het model bevestigt de opgeslagen toelating;
een volgende beurt kan een andere taak starten, status lezen of gericht stoppen.
De bestaande hulpagent is uitsluitend voor denken/schrijven/analyse zonder tools.
Dit levert geen algemene achtergrondbrowser of dynamische keuze uit meerdere
workerprofielen: het bestaande verzegelde workerprofiel blijft ongewijzigd.

## Wijzigingen

- `src/eva/application/conversation.py`: optioneel contactmodel, routering op
  daadwerkelijke receipts, achtergrondtoelating zonder voorgrondpolling,
  autonomie blijft op bestaande werkmodelroute.
- `src/eva/bootstrap/runtime.py`: opt-in samenstelling met dezelfde credentials,
  budgetten en toolcatalogus. Zonder nieuwe instelling is de bestaande route actief.
- `tests/application/test_contact_model.py`: eerste reactie, context, geheugen-
  versus echte toolstap, voice-vervolg, weigering, usage-journal en autonomie.
- `tests/bootstrap/test_runtime_settings.py`: opt-in en validatie.
- `tests/postgres/test_delegated_work_owner_service.py`: echte database-toelating,
  actieve call, tweede taak, gesprek en gerichte stop/correctie met vervangende taak.

## Begrensd bewijs en verbruik

180 gerichte tests slagen; zie `fast-contact-tests-2026-09-22.xml`. De nieuwe
integratieproef gebruikt PostgreSQL 16.13 in een uitsluitend voor deze kandidaat
gemaakte container. Zij doorloopt de echte action-adapter, owner-service en store;
de actieve providercall is gecontroleerd geblokkeerd, geen externe werkmodelcall.
Eerdere testfixturefouten (tijdstempels, veldnamen en hergebruik van een niet-lege
wegwerpdatabase) zijn hersteld; geen productcontrole afgezwakt.

Zes afzonderlijke `PydanticConversationModel.generate`-calls op de bestaande
ChatGPT/Codex-providerroute, ieder geval eenmaal per model; geen herhaalronde:

| Geval | Astra medium eerste tekst / klaar | Luna low eerste tekst / klaar |
|---|---:|---:|
| Hallo | 4472 / 4969 ms | 1597 / 1974 ms |
| Twee plus twee | 2183 / 2406 ms | 1193 / 1484 ms |
| Bevestiging van toegelaten werk | 2719 / 3880 ms | 1484 / 2035 ms |

De proef gebruikt dezelfde synthetische context en bij het derde geval een
synthetische toelatingsreceipt; zij maakt geen echte achtergrondtaak. Beide
modellen antwoorden passend op groet en rekensom en leveren tekst na toelating,
zonder polling. Luna formuleert de laatste bevestiging als bedoeling; de
werkelijke taakselectie en toelating-plus-bevestiging samen zijn hiermee niet
bewezen. Productiecontext bevat meer tools en mogelijk meer geheugen.

Gemeten totaal: **6 modelpogingen, 7616 inputtokens, 102 outputtokens, 0 toolcalls**.
Geen herproef of nieuwe betaalroute. Abonnementseurokosten en Codex-bouwtokens zijn
onbekend; dit is geen nul-kostenclaim. Raming voor bouw/test/review was 60–120
minuten en zes generate-calls; ontwikkeltijd is niet met een aparte timer gemeten.
Bron voor timing en antwoorden: `fast-contact-model-timing-2026-09-22.json`.
Herhaalbare, expliciet te starten probe: `fast-contact-probe-2026-09-22.py`.

Een onafhankelijke reviewer `/root/contact_review` vond na herreview geen
blokkerende codebevinding en controleerde het test- en timingbewijs. Review is
code/bewijsinspectie, geen extra provider- of fysieke proef.

## Integratie door de coördinator

De gedeelde bronbasis is inmiddels `26ec1b2e5812aebed421bc26f441d43cd0e4f3c7`.
De vier bestaande gewijzigde code-/testbestanden zijn byte-inhoudelijk gelijk
tussen die basis en onze oorspronkelijke bronkopie `65907604`. De latere
migratiekopie `00f7001a` is alleen testinfrastructuur. **Geen van beide
snapshotcommits integreren.** Cherry-pick uitsluitend de featurecommit die bij
deze overdracht wordt genoemd, of pas diens diff op de huidige checkout toe.
WERKBORD/JSON zijn bewust niet onderdeel van de featurecommit; gebruik onderstaand
statusfragment voor de inmiddels verder bijgewerkte centrale bron.

Na code-integratie, imagebouw en normale releasecontrole kan de coördinator in
een kandidaatconfiguratie kiezen voor:

```text
EVA_CONVERSATION_CONTACT_MODEL=gpt-5.6-luna
EVA_CONVERSATION_CONTACT_REASONING_EFFORT=low
```

Behoud het bestaande `EVA_CONVERSATION_MODEL` en de werkmodelinstellingen. De
hulpagent blijft onder de bestaande activering en eigenaarbinding vallen. De
terugweg is het contactmodelveld weglaten: de oorspronkelijke route wordt dan
weer gebruikt. Deze taak heeft geen live configuratie of installatie gewijzigd.

Gerichte regressie, vanuit `eva2`, met gepinde bestaande dependencies, pytest en
een **lege wegwerpdatabase** PostgreSQL 16.13 (de naam moet beginnen met
`eva2_4b_disposable_`; nooit een livedatabase):

```text
PYTHONPATH=src:. EVA2_TEST_POSTGRES_DSN=<wegwerpdsn> python -m pytest -q \
  tests/postgres/test_delegated_work_owner_service.py \
  tests/postgres/test_delegated_work_actions.py \
  tests/application/test_contact_model.py tests/application/test_conversation.py \
  tests/application/test_voice_context.py tests/bootstrap/test_runtime_settings.py \
  tests/application/test_memory_audience.py \
  tests/bootstrap/test_native_household_responder.py \
  tests/bootstrap/test_household_voice_authority.py tests/api/test_native_voice.py
```

Na afzonderlijke installatie: probeer Hallo, een eenvoudige vraag, een
achtergrondschrijfopdracht, nog een opdracht, een korte vraag tussendoor en een
gerichte correctie/stop met het teruggegeven work_id. Controleer taakstatus en
opgeslagen resultaat; start niet slechts op basis van Eva's bevestiging. Vergelijk
eerste hoorbare inhoud en kwaliteit via Telegram, lokale website en de bedoelde
HA/Voice PE-ingang. De loopbacktoegang en bestaande voice-ontvangstzin zijn behouden.

Open: werkelijke modelgestuurde taakselectie bij brede toolcatalogus, algemene
webresearch op de achtergrond, automatische resultaatterugkeer over alle kanalen,
integrale latency/p95, fysieke voicepariteit en eigenaaracceptatie. Native
HA/Voice PE wacht nog op een volledige respons. Household-device wordt geen
geverifieerde spreker; privégeheugen op gedeelde voice blijft gebonden aan de
bestaande grenzen. Deze kandidaat bewijst geen nieuwe sprekerherkenning.

## Statusfragment voor het bestaande centrale bord

Technisch geteste integratiekandidaat: optioneel snel contactmodel in één kern;
bestaand werkmodel en duurzame taakroutes blijven behouden. 180 gerichte tests
groen, inclusief echte PostgreSQL-taaktoelating, doorpraten tijdens call_started
en gerichte stop/correctie. Zes synthetische modelcalls: Hallo eerste tekst
4,47 → 1,60 s; totaal 7616 input- en 102 outputtokens. Review zonder blocker.
Niet geïnstalleerd; geen integrale voice-/p95-/kanaalpariteits- of
eigenaaracceptatieclaim. Volgende stap: feature-integratie op 26ec1b2e, reguliere
image-/releasecontrole en apart geplande kanaalproef.
