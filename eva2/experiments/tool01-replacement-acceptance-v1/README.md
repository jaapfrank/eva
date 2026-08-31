# TOOL01 replacement acceptance

Deze geïsoleerde checker valideert bewijs voor één providerneutrale toolactie.
Hij wijzigt geen productcode, start geen provider en doet geen modelcall.

De synthetische tests bewijzen dat de checker zelf gesloten faalt bij onder
meer een tweede effect, een niet-afzonderlijke readback, ontbrekend duurzaam
admissionbewijs, hardgecodeerde toolnamen, artifactdrift en een tweede
runtime-eigenaar.

De runtimeproef gebruikt later exact deze zichtbare prompt:

```text
Ik zit nu in de overkapping en het is hier koud.
```

De MCP-fixtures publiceren pas na processtart nonce-gebonden namen voor een
observe-, effect- en readbacktool plus één niet-passende tool van een tweede
provider. De checker vereist vervolgens:

- een door het model gekozen `DeferredToolRequests`-effect;
- uitvoering uitsluitend via `ConversationActionPort`;
- een duurzaam admission-event vóór provider-I/O;
- exact één effect en een afzonderlijke readback;
- een kill tussen effectacceptatie en readback;
- herstel zonder tweede mutatie;
- een exacte replay zonder providercall;
- een eindantwoord gevoed met `DeferredToolResults` en dezelfde frameworkhistory.

`generate_source_authority.py` scant daarvoor alle Pythonbron onder `src/eva`,
bindt pad, bytegrootte en SHA-256 in één bronmanifest en leidt de runtime-
eigenaar af uit de werkelijk geconstrueerde conversation-modelklasse in
`bootstrap/runtime.py`. De checker scant de aangewezen worktree opnieuw; een
artifact kan verwijderde code dus niet alleen beweren.

`sourceAuthority.status=pending` kan alleen `source_authority_pending`
opleveren, ook wanneer de runtime groen is. De eindstatus
`replacement_passed` vereist daarnaast exact één
`PydanticConversationModel`-toolloop en runtime-eigenaar, plus werkelijke
afwezigheid van de opgegeven oude paden en symbolen. Een verplaatst of hernoemd
entrypoint, tweede eigenaar of vervalste verwijderingsclaim faalt gesloten.

Een `passed`- of `allPassed`-veld is overal verboden. De checker leidt zijn
uitkomst af uit de gehashte catalogus-, traject-, ledger-, provider-, restart-
en eventueel bronartifacts.

Lokaal uitvoeren:

```bash
python3 -m pytest -q \
  eva2/experiments/tool01-replacement-acceptance-v1/test_acceptance.py
python3 eva2/experiments/tool01-replacement-acceptance-v1/check_acceptance.py \
  /pad/naar/bundle.json --source-root /pad/naar/eva2

python3 \
  eva2/experiments/tool01-replacement-acceptance-v1/generate_source_authority.py \
  eva2 --run-id tool01-review-001 --output /pad/naar/source-authority.json
```

De generator geeft exitcode `0` bij een complete authority, `1` zolang eerlijke
vervangingsblokkers bestaan en `2` bij ongeldige invoer of een onleesbare bron.

Dit is geen live bewijs, geen productintegratie en geen vierwegscore.
