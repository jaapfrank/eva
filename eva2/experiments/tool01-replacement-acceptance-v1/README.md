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

`sourceAuthority.status=pending` kan alleen
`source_authority_pending` opleveren, ook wanneer de runtime groen is. De
eindstatus `replacement_passed` vereist daarnaast een gehashte bronauthority
met exact één `PydanticConversationModel`-toolloop en zonder de opgegeven oude
paden of symbolen.

Een `passed`- of `allPassed`-veld is overal verboden. De checker leidt zijn
uitkomst af uit de gehashte catalogus-, traject-, ledger-, provider-, restart-
en eventueel bronartifacts.

Lokaal uitvoeren:

```bash
python3 -m pytest -q \
  eva2/experiments/tool01-replacement-acceptance-v1/test_acceptance.py
python3 eva2/experiments/tool01-replacement-acceptance-v1/check_acceptance.py \
  /pad/naar/bundle.json
```

Dit is geen live bewijs, geen productintegratie en geen vierwegscore.
