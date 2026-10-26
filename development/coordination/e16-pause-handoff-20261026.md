# Eva2-overdracht bij eigenaarpauze — 26 oktober 2026

Jaap heeft alle vijf ontwikkelchats stilgezet wegens gewijzigd abonnement en lager gewenst verbruik. Alle vier andere chats bevestigden dat geen muterende opdracht meer loopt. Alleen overdracht en registratie zijn daarna bijgewerkt. Niet hervatten zonder expliciete eigenaaropdracht. De app meldt dat automatisering `eva2-taken-afronden` niet meer bestaat; niet opnieuw aanmaken.

## Bewaarde oplevering

- E1.6 blijft open op oorspronkelijke criteria: echte modelgekozen bronkeuze, bronverandering, afweging en gecontroleerde uitkomst zijn niet als volledige keten bewezen.
- De eerdere gezamenlijke E1.6/E4.16-correctie is25oktober22:05UTC geïnstalleerd en onafhankelijk gecontroleerd. Niet opnieuw bouwen of installeren.
- De gewone22:15-afweging slaagde en koos stilte zonder bronwatch. De volgende gewone afweging498047 eindigde23:26:23UTC als WILL_USAGE_UNCERTAIN, revision4. Concrete exceptionbranch, HTTP-status, upstreamoorzaak en verbruik blijven onbekend. De vier oorspronkelijke gebeurtenissen blijven bewaard; voor dit incident is geen backfill, retry, abandon of credentialsync uitgevoerd.
- De nieuwe statusdiagnostiek is gebouwd, offline getest en onafhankelijk gecontroleerd. Alleen een werkelijk gevangen HTTPError-status van exact type int100..599 komt in de bestaande foutregistratie; geen antwoordtekst, headers, tokens of verzonnen oorzaak. Codecommit: `3d51c22b`. Bewijs: `development/evidence/product/e16-http-status-20261026.json`.
- Het nieuwe pakket is gebouwd en onafhankelijk beoordeeld, maar NIET geïnstalleerd. Pakket: `/var/lib/eva2/e16-http-status-20261026`. Handoff SHA256 `10df34db505c3118b7ed06b6e554af52502335830b1d102cf360d25cf187588b`; pakketreview SHA256 `8455c4cf760d1ac527093e69ce5bdb8499817698c6194f555685d3ce06d6f682`.
- Kandidaatcore: `sha256:5fabc30e0ab32ab6d0b920aaf9c35179d69f3d273a4d6cd256414321f2d3e769`; kandidaatworker: `sha256:b5aea2f0e6bf89a6b0785c018cb589f276ab3e0accdb7293e2a7772bb5b8051b`. Exact twee bronbestanden per rol; overige439/428 bronbestanden en rolverschillen behouden. Alle vier kandidaatprofielen compatibel. Programmaprofielen voor digitaal, openbaar en huishoudelijk onderzoek veranderen; alleen één actieve hostproducerpin verandert. Alle88 hostbestanden en historische rondes blijven behouden.

## Exacte hervatgrens

`install-http-status.py` is een ONVOLTOOID, NIET BEOORDEELD concept. De voorbereiding van `installer-binding.private.json` stopte op een TypeError: `host['frozen_manifest']` is een dictionary met `path`/`sha256`, geen padstring. Er bestaat geen installerbinding, installatie-GO, `installation.private.json` of `runtime-update.private.json` voor dit pakket. Start het concept niet als uitvoeringsklare installer.

Alleen na expliciete hervatting: herstel en voltooi de beperkte installer, inclusief het manifestgebruik in het script zelf. Laat script en bindings onafhankelijk beoordelen. Controleer vlak voor eventuele installatie opnieuw de werkelijk huidige images/configuratie, vier profielen, eigenaarbevoegdheid/STOP en actieve gespreks-, model-, werk- en hostbeoordelingsclaims. Eerdere nulmetingen zijn geen actuele toestemming. Behoud data, rolverschillen en vreemde wijzigingen; controleer na installatie de werkelijk geïnstalleerde bronnen en profielen. Geen oude installer/recovery, modelprobe of historische foutafhandeling herhalen.

Deze overdracht sluit geen volledige kaart of het project. Er is geen nieuwe eigenaarproef aangevraagd en geen runtimewijziging uitgevoerd tijdens deze administratieve afronding.
