# Node/MCP onderhoud — 6 november 2026

Deze recepten bouwen gerichte overlays op de exact behouden lokale referentie-images. Ze reproduceren de vastgelegde runtime- en bronpayload; image-ID’s en tijdafhankelijke RPM-installatiemetadata zijn geen reproduceerclaim. Er wordt geen volledige, mogelijk gewijzigde werkmap gebouwd.

De vijf bestaande providers gebruiken drie referentie-images. Alleen de workspace start filesystem-server2.2.0; de vier documentproviders blijven hun bestaande atomische server met precies twee tools gebruiken. Die server importeert SDK1.x uit dezelfde npm-boom, daarom blijft SDK1.32.2 expliciet opgenomen. Node26.13.0 en exact de vereiste, ondertekende RedHat-libatomic worden toegevoegd. Python/ContextForge1.0.8/MCP1.29 blijven in deze batch gelijk.

De eigenaar had al zes filesystemacties toegelaten. Het bestaande core/worker-factorycatalogus telt negen acties; de bestaande goalworker vier. De bekende2.2-schema’s worden uitsluitend voor de bestaande toelating herkend. Oude catalogushashes, bevoegdheden en de gepubliceerde gatewayselectie van14filesystemtools en vier maal twee atomische tools worden niet herschreven. De twee extra upstreamroutes krijgen geen toelating.

## Context maken

Gebruik voor iedere opdracht een nieuwe uitvoermap. Alleen providers hebben de publieke Node-download en `npm ci --ignore-scripts --engine-strict` nodig; gebruik desgewenst `--node-archive` voor het geverifieerde archief uit lokale opslag.

```sh
python3 prepare_context.py provider --role workspace --output /tmp/eva-node-workspace
python3 prepare_context.py provider --role atomic --output /tmp/eva-node-atomic
python3 prepare_context.py provider --role projects --output /tmp/eva-node-projects
python3 prepare_context.py core --role core --repository /home/administrator/project/Eva2 --output /tmp/eva-node-core
python3 prepare_context.py core --role worker --repository /home/administrator/project/Eva2 --output /tmp/eva-node-worker
```

Bouw met `docker build --build-arg BASE_IMAGE=<manifestreferentie> --tag <lokale-naam> <uitvoermap>`. Voor core/worker kan het bouwen netwerkloos. Voor providers haalt microdnf alleen de exact benoemde libatomic-versie op; de bestaande RPM-versies blijven behouden.

## Payload controleren

```sh
python3 verify_payload.py provider --role workspace --image <lokaal-image>
python3 verify_payload.py core --role core --image <lokaal-image>
```

Herhaal dit voor de andere rollen. Verificatie gebruikt netwerkloze, alleen-lezen containers zonder volumes, tokens, databasegegevens, modelaanvragen of gebruikerstaak.

De core/worker-overlays wijzigen precies drie werkelijk geïmporteerde bestanden onder `/opt/eva/src/eva`. Alle bestaande site-packages en wheel-RECORDs blijven bytegelijk: RECORD controleert de behouden wheellocatie, het afzonderlijke bronmanifest de gebruikte `/opt`-bron. De vier runtimeprofielen en eigenaar-/STOP-/lease-/gatewayproeven zijn daarnaast afzonderlijk bewezen.

Installatie gebruikt een apart beoordeeld privéplan met actuele claims, bestaande opslag, netwerk- en bronbindingen. Privéconfiguratie en inlogmiddelen horen niet bij dit publieke bouwrecept. Dit recept voert geen installatie uit en claimt geen volledige autonome E4.16-kringloop of gezinsacceptatie.
