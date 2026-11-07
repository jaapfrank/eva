# Eva — kernarchitectuur en grenzen

Dit beschrijft **verantwoordelijkheden**, niet automatisch geïnstalleerde werking. Het [projectdoel](PROJECTDOEL.md) bepaalt de uitkomst; alleen [het JSON-werkbord](../design/product/execution-workboard-v1.json) bepaalt taakstatus en bewijs.

## Eén samenhangende uitvoering

```text
Toegestane chat / spraak / bronnen
                ↓
     identiteit en mandaten
                ↓
  context, PostgreSQL en Hindsight
                ↓
   afweging: actie, vraag of stilte
                ↓
   duurzame taak en toolcontract
                ↓
 effect/receipt → terugkoppeling
                ↓
       leren en later gebruik
```

Er komt geen tweede geheugen, scheduler, agentplatform of Eva per kanaal. Eenvoudige bekende acties kunnen zonder model; modelgebruik is voor betekenis en onzekerheid, met gemeten kosten en limieten.

## Wie is waarvoor verantwoordelijk?

| Onderdeel | Verantwoordelijkheid |
| --- | --- |
| `contracts/` en `application/` | Privacy, actuele bevoegdheid, identiteit, taak- en toolcontracten |
| `adapters/api/` en `adapters/connectors/` | Web/dashboard, Telegram, Home Assistant, browser en andere optionele kanalen |
| `adapters/model/`, `adapters/projection/` | Modeltoegang, context, Hindsight-geheugenprojectie |
| `adapters/postgres/` | Canonieke opslag, werkstatus, claims, bronnen en ontvangstrechten |
| `operations/` en `bootstrap/` | Duurzame uitvoering, herstel, gecontroleerde releases en samenstelling |

Spraaktransport (waaronder LiveKit/WebRTC) is niet hetzelfde als natuurlijke beurtgrenzen, realtime onderbreking of speakeracceptatie. Nieuwe vaardigheden en uitbreidingen blijven buiten de algemene kern, gebruiken bestaande module-/procedureinterfaces en zijn versieerbaar en herstelbaar.

## Veiligheid en bewezen gedrag behouden

- Externe tekst, imports, prompts en modelantwoorden verlenen nooit bevoegdheid. Controleer de actuele identiteit, rechten, ontvanger en intrekking vlak vóór een effect.
- Een externe actie krijgt vóór uitvoering een duurzame claim. Alleen een passend ontvangstbewijs bevestigt voltooiing; onzekere effecten worden niet blind herhaald.
- Stoppen, minimale rechten, persoonsgegevens scheiden, wissen/corrigeren, historische fouten behouden en herstel naar een bekende versie zijn verplicht.
- Zelfgegenereerde code wordt geïsoleerd uitgevoerd en pas na gecontroleerde kwaliteit, onafhankelijke review en readback toegepast. Een reviewersnaam alleen bewijst geen onafhankelijkheid. Nieuwe componentreviews binden het volledige kandidaatpakket en acceptatiebesluit aan de bestaande hostregistratie, vaste beoordelingscode, bron en baseline. Die toestemming komt uit hostconfiguratie; een reviewbestand kan haar niet zelf aanwijzen. Oude v1-bewijzen blijven historie en verlenen geen nieuwe uitvoerbevoegdheid.
### Kinderen en gedeelde toegang

- Een herkenbare stem, gezicht, naam, Telegram-groep of speaker is onvoldoende om iemands identiteit of leeftijd vast te stellen. Een gedeelde speaker gebruikt standaard de gedeelde apparaatrol. Die rol kan uitsluitend de expliciet toegelaten gedeelde functies gebruiken. Groepstoestemming op Telegram geldt voor dat gesprek en de gedeelde bijdrage.
- Persoonlijke functies voor een kind worden pas vrijgegeven na expliciete, intrekbare toestemming van een geverifieerde ouder of voogd, een eigen afgeschermde identiteit en een aparte proef op dezelfde geïnstalleerde versie. Leg vooraf vast welk doel en welke gegevens nodig zijn. Laat kindprofielen, persoonlijke opslag voor kinderen en gebruik van hun camera- of stemkenmerken voor herkenning gesloten totdat die voorwaarden zijn aangetoond. Apparaattoestemming of een bevestigde persoonlijke speakersessie geldt uitsluitend voor de eigen vastgelegde reikwijdte.
- Een gedeeld gesprek kan onder het huishoudmandaat bewaard blijven. Open persoonlijke informatie alleen via een afgeschermd kanaal met actuele bevoegdheid. Bij intrekking of verval stopt het gebruik van die toestemming meteen. Een persoonlijk resultaat op een gedeelde speaker vraagt een actuele bevestiging vlak vóór het beschikbaar stellen.
- Gebruik **Stoppen** bij het werk in het eigenaardashboard. Open een geheugenbron via **Bron bekijken en aanpassen** en bevestig **Deze bron vergeten** om die bron te wissen. Intrekken van Telegram-groepstoestemming kan via het gekoppelde privégesprek met `/stop-sharing`. Stoppen, intrekken en wissen hebben elk hun eigen werking. Voor brede gezinsuitrol moeten deze handelingen zichtbaar en bruikbaar zijn voor de betrokken persoon en diens bevoegde ouder of voogd.

## Vereenvoudigingsgrens

Verwijder aantoonbare duplicatie van claims, transacties, kanaalbinding en foutafhandeling, maar vervang **geen werkende veiligheidsketen** door een nieuwe frameworklaag. Scheid selecteren, autoriseren, claimen, afleveren en controleren. Grootte van een klasse op zichzelf is geen bewijs dat de code overbodig is.

Een concreet voorstel staat in [VEREENVOUDIGING](VEREENVOUDIGING.md); het werk en de voortgang in [AFRONDINGSPLAN](AFRONDINGSPLAN.md). [Oorspronkelijke technische uitwerking](../archive/history/20261105-documentatie-opruiming/ARCHITECTUUR.md).
