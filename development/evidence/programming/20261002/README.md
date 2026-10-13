# Agenda-exporten vergelijken

Gebruik Python 3 (alleen standaardbibliotheek):

```sh
python3 /workspace/agenda_changes.py /workspace/before.json /workspace/after.json /workspace/changes.json
```

CLI: `agenda_changes.py BEFORE AFTER OUTPUT`.
De terminal toont aantallen in het Nederlands. OUTPUT bevat UTF-8 JSON:
- `added`: nieuwe objecten;
- `removed`: oude objecten;
- `changed`: objecten met `id`, `before` en `after`;
- `unchangedIds`: ongewijzigde ids.

Alle lijsten zijn op id gesorteerd. De vergelijking gebruikt alle velden, ook geneste waarden; objectvolgorde telt niet, lijstvolgorde wel. JSON-typen tellen mee (ook integer versus float). Quotes, Unicode en overige veldwaarden blijven behouden; oorspronkelijke witruimte en escape-notatie niet.

Elke invoer moet een UTF-8 JSON-lijst van objecten zijn met unieke ids en nietlege strings voor id, title en start. Alleen witruimte geldt als leeg. Dubbele objectsleutels en niet-standaard constanten zoals NaN worden afgewezen. Datums worden niet geïnterpreteerd of gevalideerd. Geen netwerk of agenda-integratie.

Bij fouten verschijnt een leesbare fout op stderr en een niet-nul exitcode. Invoer wordt niet beschreven; OUTPUT mag ook niet via een symlink of hardlink een invoerbestand zijn. Na validatie wordt de uitvoer via een tijdelijk bestand atomair vervangen. Een bestaand OUTPUT blijft bij ongeldige invoer onaangetast.

Beperkingen: alles wordt in het geheugen geladen; JSON-getallen gebruiken de normale Python int/float-representatie (geen garantie op behoud van uitzonderlijk hoge decimale precisie). Geen beveiliging tegen gelijktijdige externe bestandswijzigingen.
