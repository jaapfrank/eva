# Eva 2 architecture bakeoff

Dit is een wegwerpbare architectuurproef. De code importeert niets uit Eva 1, opent geen Eva 1
database en start geen productservice.

Het uitvoeringscontract staat in
[`docs/eva2-architecture-bakeoff-v1.md`](../../docs/eva2-architecture-bakeoff-v1.md).

De directory bevat uiteindelijk precies één gedeelde fixture, één gedeeld domeincontract, drie
durable adapters en één runner. Alle adapters krijgen dezelfde Pythonversie, modeladapter,
effectbroker en scenario's. Resultaten worden als bewijsbundel geschreven; een groene run kiest
op zichzelf geen winnaar.

Vastgepinde eerste vergelijking:

- Restate Server 1.7.2 met `restate_sdk==1.0.4`;
- DBOS Python `2.30.0` met PostgreSQL als scorende opslag;
- een kleine SQL-state-machine als controlebaseline;
- `pydantic-ai-slim==2.32.1` voor dezelfde deterministische modeladapter, zodat ongebruikte
  provider-, UI- en MCP-extras niet als frameworklast worden meegeteld.

De fixture luistert alleen op localhost en gebruikt een eigen SQLite-bestand buiten iedere
kandidaatdatabase. Daardoor kan een kandidaat een extern effect uitsluitend via de API en haar
readback bewijzen.
