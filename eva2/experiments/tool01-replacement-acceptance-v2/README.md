# TOOL01 replacement acceptance v2

TOOL01 v2 controleert één echte, providerneutrale toolactie over drie nieuwe
Eva-containers. De checker start zelf geen model, database, container of
provider en vertrouwt geen `passed`-veld.

De modelopdracht is bewust een echte bestandstaak. Een eerdere kandidaat
koppelde de zin “ik heb het koud” aan een kale bestandsschrijftool; dat zou een
model juist belonen voor een semantisch verkeerde toolkeuze. De afzonderlijke
comfortproef moet een werkelijk passende, dynamisch ontdekte apparaattool
gebruiken. Deze poort bewijst alleen dat Eva een onbekende passende tool kiest,
duurzaam uitvoert, onafhankelijk terugleest en na een crash niet dubbel
uitvoert.

De filesystem-provider krijgt daarbij een eigen kortlevende, private
opstartsleutelset. De beheersleutels van de centrale ContextForge-gateway
worden niet met deze verwisselbare provider gedeeld.

V2 gebruikt uitsluitend de gebeurtenissen die de huidige algemene toolroute
werkelijk duurzaam schrijft:

```text
TOOL_REQUEST_ADMITTED -> TOOL_PREPARED -> STATE_VERIFIED
```

De eerste container wordt gestopt nadat de externe provider één execute heeft
geregistreerd, maar voordat Eva een dispatchresultaat heeft opgeslagen. Een
externe Docker-audit moet voor die container `KILL`, exitcode `137` en
`OOMKilled=false` aantonen. De tweede container hervat dezelfde PostgreSQL-
thread en mag uitsluitend één afzonderlijke observe/readback uitvoeren. Een
derde container herhaalt dezelfde aanvraag en moet zonder databasewijziging of
providercall exact dezelfde receipt terugkrijgen.

Vier tellerstanden komen rechtstreeks uit dezelfde externe providerinstantie:

| Meetpunt | Execute | Observe |
|---|---:|---:|
| Vóór uitvoering | basis | basis |
| Na execute, vóór kill | +1 | +0 |
| Na hervatten | +1 | +1 |
| Na replay in derde container | +1 | +1 |

Alle drie containers moeten unieke volledige Docker-ID's hebben en exact het
extern opgegeven content-image-ID en broncommitlabel gebruiken. Voor
`replacement_passed` wordt bovendien de bestaande v1-bronauthority opnieuw
tegen een echte checkout berekend en moet haar commit gelijk zijn aan het
bronlabel van het geteste image.

Ook de SHA-256 van het container-interne proefprogramma is een externe
checkerinput. Bereken die buiten de kandidaatcontainer uit exact
`prove_filesystem_provider_sigkill_v2.py`. De hash die de kandidaat zelf in
`candidateAuthority.json` schrijft is alleen een te vergelijken claim en kan
deze externe authority niet vervangen.

`hostAuthority.json` is eveneens verplicht. De checker bindt daarin de
kandidaatimage, kandidaatcommit, proefprogrammacommit en proefprogrammahash aan
de externe CLI-waarden. De lokale image-ID's voor filesystem, tijdprovider,
ContextForge en PostgreSQL worden elk afzonderlijk extern opgegeven. Ook wordt
`contextforgeStartSha256` opnieuw berekend uit het schone kandidaatcheckout:
het bestand moet op exact
`eva2/experiments/contextforge-gateway-v1/start-contextforge.sh` door dezelfde
commit worden gevolgd en bytegelijk aan `git show` zijn.

De checker wordt zo uitgevoerd:

```bash
python3 experiments/tool01-replacement-acceptance-v2/check_acceptance.py \
  /pad/naar/bundle.json \
  --expected-contextforge-image-id sha256:<64-hex> \
  --expected-filesystem-image-id sha256:<64-hex> \
  --expected-image-id sha256:<64-hex> \
  --expected-postgres-image-id sha256:<64-hex> \
  --expected-proof-harness-sha256 <64-hex> \
  --expected-proof-source-commit <40-hex> \
  --expected-source-commit <40-hex> \
  --expected-time-image-id sha256:<64-hex> \
  --source-root /pad/naar/exacte-checkout
```

Zonder complete bronauthority kan een groene runtime alleen
`source_authority_pending` opleveren. Een geclaimd image-ID in het bundle kan
de verplichte externe CLI-authority niet vervangen.

Gerichte checkertests:

```bash
python3 -m pytest -q \
  experiments/tool01-replacement-acceptance-v2/test_acceptance.py
```

V1 blijft ongewijzigd als historisch ontwerp. Zijn gebeurtenissen
`PROVIDER_CALL_STARTED`, `PROVIDER_ACCEPTED` en `READBACK_CALL_STARTED` zijn
geen gebeurtenissen van de huidige generieke tooltimeline en gelden daarom
niet als v2-bewijs. V2 leidt afzonderlijke execute en observe uitsluitend af
uit de externe providercalls en tellerdelta's.

Uitvoeren en teruglezen hebben niet hetzelfde invoerschema. Deze concrete
acceptatiepoort berekent de twee opdrachten daarom zelf opnieuw uit de
modelargumenten: `write_file(path, content)` moet exact dezelfde `path` en
`content` ontvangen, `read_text_file(path)` exact hetzelfde pad en de bewezen
uitkomst moet exact `{"content": content}` zijn. De duurzame admission,
`TOOL_PREPARED` en externe providerartifact moeten daarnaast dezelfde
afzonderlijke argumenthashes en configuratiehash dragen. Daardoor kan een
intern consistente maar van het model losgeraakte bewijsbundel niet slagen.

V2 gebruikt evenmin één verzonnen volgnummer over database-, provider- en
Dockerartifacts heen. Iedere bron bewaart zijn eigen volgorde; hashes,
container-ID's, operation-ID en de vier vaste meetfasen leggen de onderlinge
binding vast. Een losse derde `observe`-catalogusrol is verwijderd: de externe
observe-call gebruikt de afzonderlijke readbacktool. Exitcode `137` alleen is
niet genoeg zonder de externe `KILL`-opdracht en `OOMKilled=false`.

Dit experiment is geen live promotie, geen providerimplementatie en geen
bewijs voor providers zonder onafhankelijke readback.
