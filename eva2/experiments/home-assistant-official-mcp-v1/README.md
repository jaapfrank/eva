# Officiële Home Assistant MCP/Assist-kandidaat

Deze proef vervangt geen apparaatgroepen door nieuwe Eva-code. Eén generieke
client leest de officiële `/api/mcp/assist`-catalogus, bindt die aan een hash en
roept alleen vooraf toegelaten Assist-tools aan.

Eva houdt drie eigen grenzen:

1. persoon en toestemming worden vóór de MCP-call gecontroleerd;
2. de toolnaam, catalogus, invoerschema en verplichte argumenten zijn gebonden;
3. succes wordt daarna apart via de Home Assistant-statusroute teruggelezen.

Dat laatste blijft nodig omdat een geslaagde MCP-call alleen zegt dat Assist de
opdracht accepteerde. Het bewijst nog niet dat het fysieke apparaat de bedoelde
toestand heeft bereikt.

## Waarom dit nog een kandidaat is

De officiële MCP Server is op 30 augustus 2026 in Home Assistant 2026.8.1
geconfigureerd. De live catalogus bevatte 25 tools en `GetLiveContext` werkte
zonder een huisactie uit te voeren. De route zit nog niet in Eva's normale
persoonsgebonden actieketen.

De officiële server laat in deze Home Assistant-versie bovendien bij sommige
tools de lijst met verplichte argumenten weg. De kandidaat vult die grens niet
met een gok van het model aan: een lokale toelating moet de verplichte velden
expliciet noemen en is aan het waargenomen schemaspoor gebonden.

De volgende stap is één omkeerbare, eigenaar-gebonden testactie met een
onafhankelijke state-readback. Daarna kan dezelfde generieke route alle bewust
aan Assist blootgestelde apparaten en scripts gebruiken; daarvoor komt geen
aparte Eva-implementatie per apparaatgroep.

## Testen

```bash
PYTHONPATH=src:. pytest -q -s experiments/home-assistant-official-mcp-v1/test_candidate.py
```

De tests gebruiken alleen een lokale synthetische Home Assistant-MCP-server.
Zij wijzigen geen live Home Assistant, apparaat, Eva1 of Eva2-database.

`live_probe.py` kan daarna de officiële integratie één keer met uitsluitend de
ingebouwde Assist-API configureren. Bij een mislukte eerste catalogus- of
contextlezing verwijdert hij uitsluitend de entry die hij zelf zojuist maakte.
De uitvoer bevat geen token, URL, entry-ID, entitynaam, toestand of contexttekst.

`live_effect_probe.py` is de aparte effectgate. Hij weigert vóór MCP wanneer
de persoonshash niet klopt of het veilige testdoel volgens de onafhankelijke
REST-route niet al uit staat. Een toegelaten call loopt door Eva's duurzame
PostgreSQL-tijdlijn, krijgt daarna een aparte REST-readback en wordt bij exact
dezelfde replay niet opnieuw naar Home Assistant gestuurd.

De eerste live effectresponse legde een echt verschil tussen de officiële
lees- en intentcontracten bloot. Lees-tools geven `success/result`; ingebouwde
apparaat-intents geven `response_type/data`. Eva accepteert nu beide exacte
officiële vormen, maar weigert `error`, gedeeltelijke en mislukte doelen. De
verbeterde proef liep groen en een nieuw proces hield de eventtellers gelijk.
