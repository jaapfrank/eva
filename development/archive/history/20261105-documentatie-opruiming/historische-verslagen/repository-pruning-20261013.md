# Repositoryopruiming — 13 oktober 2026

857 overbodige gevolgde bestanden zijn verwijderd: **43,6 MB**. Het is een
repositoryopruiming, geen nieuwe Eva-functie of volledige functionele oplevering.

| Bestanden | Aantal | Besluit en reden |
| --- | ---: | --- |
| migration-baseline | 128 | Dubbele bronkopieën verwijderd; geen actieve import/start/build/test-read gevonden. |
| layout-baseline-61486f35 | 369 | Dubbele bronkopieën verwijderd; geen actieve import/start/build/test-read gevonden. |
| Oude Eva1 buiten benchmarkinputs | 360 | Ongebruikte tweede applicatie verwijderd. Eva2 ontwikkelt Eva1 niet verder. |
| Eva1 agentbench-fc-v1 | 33 | Behouden: bestaande tests lezen hiervan 31 concrete inputs; de twee overige horen bij dezelfde fixture. |
| Gebonden history, evidence en reviews | — | Behouden: actuele bronbindings, installerinputs, dashboard en tests lezen deze; afgesloten verliezen/UNKNOWN blijven zichtbaar. |
| src, deploy en huidige ontwikkeling | — | Behouden: ouderdom bewijst geen overbodigheid. Geen ononderbouwde moduleverwijdering. |

Elke verwijderde file is vóór verwijdering bytegelijk gecontroleerd tegen
`eab83747a3c33a83f2f63f38180dc7d6330635ec`. De
[verwijderlijst](../archive/pruning-20261013.json) bevat pad, bytes en SHA256.
Terughalen kan met `git show <revisie>:<pad>`. De oude migratiemanifesten blijven
ongewijzigde snapshots; zij beschrijven geen huidige garantie van lokale backups.

## Wat de controle wel en niet bewijst

Alle gevolgde paden zijn geïnventariseerd; de gerichte afhankelijkheidscontrole
betreft de verwijderde archiefcategorieën, gewone imports/start/buildroutes,
actuele bron-/releasebindings, dashboardlocators en bestaande tests. Er is geen
nieuwe regel-voor-regel audit van alle productcode uitgevoerd. Huidige bron heeft
nog 110 eerder geregistreerde importgrensbevindingen; negen ontbrekende historische
release-inputs zijn hiermee niet opgelost. Een rustigere root maakt Eva niet vanzelf
slimmer of af. Niet-gevolgde privébestanden, caches en runtimegegevens zijn niet verwijderd.

De onafhankelijke reviews en de noodzakelijke gerichte controles staan in
[het opruimbewijs](../evidence/reassessment/repository-pruning-20261013.json).
De productafmaakvolgorde en alle 54 kleine taakvoorwaarden blijven behouden;
43 technische afrondingen blijven 43. Eerstvolgend productresultaat blijft het
volledige opgeslagen werkresultaat naar de oorspronkelijke ontvanger.
