# E4.16 en Compose 5.7.0

Deze bouwbeschrijving bereidt de gezamenlijke wijziging voor. De bevestiging van de geïnstalleerde uitkomst komt uit de afzonderlijke onafhankelijke nacontrole.

## Bouw de bronwijziging

Gebruik de beoordeelde `src/eva/bootstrap/runtime_repair_host.py` met SHA-256 `bb196cd9b88a1d5f261b9a1978a45bdf5799e1daedcc12966b7d6f1835a78868` en de werkelijk gebruikte basis van elke rol.

| Rol | Basisimage | Beoordeeld kandidaatimage |
| --- | --- | --- |
| Kern | `sha256:f8fdd529de7908e0d63a2083fb3039bba10765ef16dc8401120e01d17801aa32` | `sha256:af0358d1e162b27f19fdef1aeece0acc0b6911d2e8534f82d8ae95f36438a926` |
| Werkworker | `sha256:c31a67092473816511d015b8b56a10a8e4504f0c50201c9dedc4ab827492bdf6` | `sha256:9322d99e6013571ad500ee6930bd6ca2181ac545805766edb01770c0ec0a4138` |

Maak per rol een lege bouwcontext met een bytegelijke kopie van de bron en dit Dockerfile. Vervang de eerste regel door de exacte basis uit de tabel. Bouw zonder netwerk of pull. De kandidaat-ID is de binding voor deze uitvoering. Verifieer de bron- en pakketinhoud apart bij een nieuwe bouw.

```dockerfile
FROM sha256:EXACTE_BASIS_UIT_DE_TABEL
COPY --chown=0:0 runtime_repair_host.py /opt/eva/src/eva/bootstrap/runtime_repair_host.py
```

Gebruik voor de host-evaluator `src/eva/operations/runtime_repair_development.py` met SHA-256 `16fe42513ba299f7342332529bf17eb39e1606639b7200a1ce47c768a785cf60`. Voeg die bron toe aan een exacte kopie van de actuele eerste bronmap. Behoud de volgorde en inhoud van de andere drie. Controleer het bronpad vanuit de werkelijke evaluatorinterpreter en de vier effectieve bronmappen. De containermodule blijft het doel van de bestaande RPC-route.

## Bewaar de bindregels

Verifieer het upstream Linux x86-64-bestand van Compose 5.7.0 tegen SHA-256 `7758048ad38e580f22559603312874fec8bb638d021a9e64182385fc17cc12a2`. Leg de werkelijk geïnstalleerde bindregels van beide containers vast voordat je kandidaatconfiguratie genereert.

Maak `bind.create_host_path: false` expliciet voor de 35 bestaande gestructureerde mounts die Compose 5.7.0 anders omzet naar het oude bindformaat. De kern heeft er 19 en de werkworker 16. De bestaande expliciete workerregel blijft behouden. Vergelijk de volledige Docker Engine-mountgegevens met een fixture die nooit start. De gecorrigeerde vorm houdt 36 gestructureerde mounts en 14 legacybinds. Vergelijk per mount bron, doel, leesrecht, propagatie en bestaand aanmaakgedrag. Weiger bij onbekende afwijking, ontbrekende bron of gewijzigde bronidentiteit. Het oude bindpad kan bij een ontbrekende bron een map aanmaken. [Moby 29.1.3 broncode](https://github.com/moby/moby/blob/docker-v29.1.3/daemon/volumes.go#L1316-L1320)

## Controleer de overdracht

Controleer dat alleen de twee beoogde images en de noodzakelijke bindrepresentatie wijzigen. Vergelijk de Compose-configuratiehashes afzonderlijk van het label op de echte containers. Houd de werk- en modelclaims, browserstatus, geheugenactiviteit, leasegrenzen en gesloten criteria bij de bestaande gecontroleerde uitvoering. Bij een onzekere terugmelding volgt inspectie van de bestaande generatie en het bewijs. Laat een tweede uitvoering achterwege.

Verifieer na de wijziging de vier brongebonden profielen in beide rollen en de containermodule in beide images en de aparte ontwikkelmodule in de host-evaluator. Verifieer de bestaande mount-, netwerk-, autorisatie- en evaluatorgrenzen en de Compose-versie als root en beheerder. Publiceer alleen de getelde resultaten en exacte bewijshashes. De volledige autonome reparatiekringloop van E4.16 vereist afzonderlijk end-to-endbewijs.
