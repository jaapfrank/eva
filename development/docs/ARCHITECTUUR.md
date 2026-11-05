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
- Zelfgegenereerde code wordt geïsoleerd uitgevoerd en pas na gecontroleerde kwaliteit, onafhankelijke review en readback toegepast. Een reviewersnaam alleen bewijst geen onafhankelijkheid.
- Voor kinderen en gedeelde ruimten zijn afzonderlijke toegangs-/toestemmingstests nodig voordat een veilige gezinsuitrol geclaimd kan worden.

## Vereenvoudigingsgrens

Verwijder aantoonbare duplicatie van claims, transacties, kanaalbinding en foutafhandeling, maar vervang **geen werkende veiligheidsketen** door een nieuwe frameworklaag. Scheid selecteren, autoriseren, claimen, afleveren en controleren. Grootte van een klasse op zichzelf is geen bewijs dat de code overbodig is.

Een concreet voorstel staat in [VEREENVOUDIGING](VEREENVOUDIGING.md); het werk en de voortgang in [AFRONDINGSPLAN](AFRONDINGSPLAN.md). [Oorspronkelijke technische uitwerking](../archive/history/20261105-documentatie-opruiming/ARCHITECTUUR.md).
