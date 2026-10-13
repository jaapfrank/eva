# UX-002 — hulpopdrachten bedienen

Tekort: de bestaande werkbordkaarten zijn alleen-lezen. De gedeelde
AU-002-eigenaarservice kan taken en resultaten al ophalen, stoppen en hervatten.
Deze wijziging ontsluit diezelfde service in het eigenaarsoverzicht.

## Geïnstalleerd — proberen

Geïnstalleerd op 22 september 2026. De echte lokale browser toont bestaande
opdrachten en hun opgeslagen resultaten; openen en vernieuwen zijn gecontroleerd.
[Installatie en bewijsgrenzen](../../evidence/product/contact-dashboard-live-2026-09-22.json).

Open http://localhost:44711/internal/v1/dashboard via de bestaande lokale
eigenaarstoegang. Hiervoor is geen handmatig dashboardtoken nodig.

1. Open onder **Mijn hulpopdrachten** een bestaande opdracht. De volledige
   opdracht, opgeslagen status en het eventuele resultaat worden leesbaar.
2. Open een lopende opdracht en kies **Stop deze hulpopdracht**. De opgeslagen
   stopstatus moet verschijnen. Een al lopende berekening kan nog aflopen;
   haar resultaat wordt niet geleverd. Stoppen is geen claim dat providerwerk
   onmiddellijk geen verbruik meer heeft.
3. Kies **Werk vernieuwen**: dezelfde opgeslagen toestand wordt opnieuw opgehaald.
4. Bij een gestopte of mislukte opdracht kan **Hervatten met nieuwe poging**
   een nieuwe begrensde poging starten. Dat kan nieuw modelverbruik opleveren.
   De vorige poging en eerder verbruik blijven bewaard.

Scope: bestaande private hulpopdrachten van de ingestelde eigenaar. Geen nieuwe
uitvoerder, taakinterpretatie, modelcall voor de bediening, geheugenbeleid of
andere kanaalidentiteit. Het projectvoortgangsbord op poort44712 is een andere
weergave; deze bediening staat in het bestaande eigenaarsoverzicht.

## Bewijs en integratie

Gerichte API- en bootstraptests controleren servergebonden persoonsgrenzen,
gedeelde serviceaanroep, hercontrole van bevoegdheid en veilige foutuitvoer.
`verify-owner-work.cjs` bedient de gebouwde frontend met geïsoleerde fixtures:
openen, letterlijke resultaattekst zonder HTML-uitvoering, stoppen, hervatten,
vernieuwen en privéinhoud verbergen bij verloren toegang.

De bestaande Postgres-servicetests blijven het bewijs voor duurzame opslag,
idempotente nieuwe pogingen en eigenaarintrekking. De nieuwe runtime-wiring
moet bij integratie dezelfde bestaande service-instance doorgeven. Geen
installatie, echte eigenaarproef of acceptatie door deze fixturetest bewezen.
Het afzonderlijke installatiebewijs hierboven beschrijft de livecontrole;
menselijke acceptatie blijft open.
