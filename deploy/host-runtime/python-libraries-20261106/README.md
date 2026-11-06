# Pythonbibliotheken voor drie bestaande hostdiensten

Deze release actualiseert de pakketomgeving van de bestaande beoordelingsdienst,
browserbeheerder en lokale gezichtsbrug. Het zijn afzonderlijke omgevingen met
precies dezelfde 33, 67 en 62 pakketfamilies; bestaande omgevingen blijven als
referentie behouden. Applicatiebron, applicatieconfiguratie, bevoegdheden en de vier bestaande
bronmappen van de beoordelingsdienst veranderen niet.

De Ubuntu-interpreter is na verversing van de ondertekende pakketlijsten al
actueel: `3.12.3-1ubuntu0.17`. Het upstream-micronummer is geen bewijs van een
ontbrekende Ubuntu-beveiligingspatch. De drie wheelsets zijn voor CPython3.12,
Linux x86_64 en glibc. PydanticAI2.66/OpenAI3.46 zijn aanwezig waar die SDK's al
geïnstalleerd waren; dit activeert geen nieuwe modelroute.

## Exacte herkomst en voorbereiding

`wheel-manifest.json` bindt alle 78 unieke officiële PyPI-wheels aan URL en SHA256.
De drie locks binden ook alle bestaande transitieve pakketten. Iedere download
is aan de specifieke officiële releasemetadata getoetst; installatie gebruikt
alleen lokale wheels met `--no-index` en `--require-hashes`. Controleer een cache
of maak nieuwe offline bouwmappen met:

```sh
python3 deploy/host-runtime/python-libraries-20261106/prepare_contexts.py --wheelhouse /path/to/verified-wheels --output /path/to/new-contexts
```

Dit kopieert pakketbestanden, geen gegevens of configuratie, en installeert geen
diensten. Maak een nieuwe venv met de genoemde Ubuntu-interpreter en installeer
de betreffende lock uit de voorbereide map. De oorspronkelijke venv wordt nooit
in-place gewijzigd. Alleen de drie bestaande service-entrypoints verwijzen naar
de nieuwe omgevingen; nette stoptijd en bescherming tegen geforceerd stoppen
worden expliciet vastgelegd. Licenties blijven onderdeel van de officiële wheels; de
projectlicentie verandert niet.

## Compatibiliteitsgrenzen

MCP1.30.0 is de nieuwste geteste compatibele1-reeks voor de beoordelingsdienst.
MCP2.3 verwijdert bestaande FastMCP- en clientinterfaces en vraagt een afzonderlijke
bronmigratie. Vier combinaties van oude/nieuwe stdio-client en -server bewijzen
initialisatie, toolresultaten, afwijzing vóór effect en nette sluiting.

Gerichte oorspronkelijke controles: beoordelingsdienst118 geslaagd,1 bestaande
overslag; browser18 en gezichtsbrug19 geslaagd, steeds op baseline én kandidaat.
Eén bredere test kan op beide omgevingen niet laden: de geïnstalleerde bevroren
hostbron mist `procedure_references`. Er is geen nieuwe productmodule geïnjecteerd
om deze bestaande beperking te verbergen. Een uitsluitend technische testbrug
verwijst de historische CLI-import naar dezelfde bevroren canonical/digest-functies.

De CPU-extractor, twee modelbestanden, profielgegevens en herkenningscriteria van
de gezichtsroute veranderen niet. Deze bibliotheekupdate bewijst geen nieuwe
herkenningsnauwkeurigheid of volledige E4.16-autonomie. Installatie en daadwerkelijke
teruglezing worden afzonderlijk gebonden aan het onderhoudsbewijs.
