# F1.2 — onafhankelijke gebundelde controle, 3 oktober 2026

**Besluit: voorbereidende gerichte controles aanvaard; F1.2 blijft open.** De echte Windows-computerherstart en de daaropvolgende controle van dezelfde gegevens, taakresultaten en gebruiksroute zijn nog niet geleverd. Dit document voltooit F1.2 niet.

Deze review gebruikt de oorspronkelijke F1-eindvoorwaarde in `design/blueprint-2026-09-28/delivery-plan.md` en de bestaande kleine taak: gebundelde eindcontrole, computerherstart en onafhankelijke review, met uitsluitend gerichte correctie van concrete fouten. F1.1 is afzonderlijk gepubliceerd op `512bdcfcac2239fc4709cdc686fee4a07c941895`. De 85 eerdere integratiecontroles en de beoordeelde L3/O1/O2/L2-uitkomsten worden hergebruikt; geen brede suite of nieuwe vergelijkingsronde.

## Gebonden geïnstalleerde versie

| Onderdeel | SHA-256 |
| --- | --- |
| Geïnstalleerde kernimage | `fd1c7cd3413405fd6b1b5ce408f2028cffed8de735a301550663e4c891ee5902` |
| Geïnstalleerde werkerimage | `30d65117cc0f526bc3ed8859a0eee867ff8fe0943c0bf42c8c09d787c2ca3089` |
| Bestaande F1.1-review | `0efcc5bab70ebb447b1948170f002b4ef2f0b15129c9fa0c8c58eee3057da9d6` |
| F1.1 onafhankelijke installed-readback | `e02fce4b4474b997ca6540899b5eca30989f1c6622a6d05750e4ed63d16bc5d7` |

## Vijf gerichte combinaties

De uitvoerder gebruikte de daadwerkelijke geïnstalleerde kernimage met haar eigen bron, Alembic-configuratie en migraties. Alleen bestaande tests en testafhankelijkheden waren aangekoppeld. PostgreSQL 16.13 draaide geïsoleerd zonder extern netwerk of productiedatabase; testcontainers deelden alleen die netwerkruimte. Er waren geen model-, provider- of huisacties. De tijdelijke PostgreSQL-container is verwijderd.

De vijf bestaande selectors leveren door parametrisatie zeven gevallen: gesprek tijdens meervoudig werk, stopcontrole vóór externe dispatch, duurzaam stop/hervatten zonder herhaalde geverifieerde handeling, hergebruik van connectorresultaatslots, en resultaatretour na herstart zonder opnieuw versturen. Uiteindelijk **7/7 geslaagd**. Dit is gecontroleerd integratiebewijs, geen echte Telegram-cloudproef of Windows-herstart.

Twee expliciete aanpassingen aan de private testkopie blijven zichtbaar:

- De bestaande fixture verwachtte migratiekop `4e_0009_restore_hold`; uitsluitend die verwachting werd gelijkgesteld aan de werkelijke imagekop `4e_0007_delegated_private`. Dit bewijst geen migratie naar 0009 en doet geen uitspraak over de actuele productiedatabase.
- De eerste normale Telegram-retourcase faalde terecht: de oude succesmock miste `reply_to_message.message_id`, terwijl de echte adapter dit eist voor de oorspronkelijke vraagbinding. De private mock spiegelt nu alleen het al aangevraagde `reply_parameters.message_id`. Alle assertions en productiebron bleven gelijk. Alleen dat mislukte geval is herhaald: 1 geslaagd in 3,87 s; de eerste 6 geslaagde gevallen zijn niet herhaald. Het oorspronkelijke foutlog blijft behouden.

De reviewer las de adapter en de exacte éénregeldiff onafhankelijk. De 346 gedeelde bron-/migratiehashes in de testinventaris stemmen overeen met de toegelaten image-inventaris. De geïnstalleerde L3-contextbron bleef `2554605179fcdff8b2b8793b8dbbdecaffdeddaafc31c65063f19e6e9269beb7`.

## Gewone route vóór herstart — tussentijdse waarneming

Het vooraf vastgelegde liveprotocol gebruikt uitsluitend expliciet synthetische testinformatie, een openbare webpagina en testbestanden via de bestaande lokale privé-eigenaarroute. De reviewer deed zelf geen model- of provideraanvragen.

De bewaarde journal toont dat de documentopdracht liep van 16:17:54 tot 16:19:18 UTC. De tussenvraag was om 16:18:02 UTC beantwoord: daadwerkelijke overlap tussen gesprek en werk is daarmee aangetoond. De ontvangst van de documentopdracht duurde 11,208 s, de tussenvraag 7,136 s en de eerdere onthoudvraag 14,01 s. Er wordt geen snelheidswinst geclaimd. Beide geregistreerde opdrachten hebben `succeeded`; de opgeslagen testbestanden zijn onafhankelijk teruggelezen.

De expliciete onthoudvraag leidde tevens tot een digitale taak die een testinformatiebestand opsloeg. Dat bestand bewijst op zichzelf geen persoonsgebonden geheugenprojectie. De concrete geheugen-/outbox-/projectierecords en reguliere terugkeer van het werkresultaat moeten nog aan het herstartbewijs worden gebonden. De op dit moment beoordeelde terugkeerhistorie was leeg; daaruit volgt nog geen geslaagde live resultaatterugkeer. De definitieve runtimeverbruikssamenvatting volgt uit de complete bestaande journal; onbekende kosten blijven onbekend.

## Nog nodig binnen de bestaande F1.2-eindvoorwaarde

Bind vóór de eigenaarherstart de actuele bron/image-/configuratiehashes, relevante Docker- én hostservicebindingen, dezelfde eigenaar-/geheugenscope en de concrete testgeheugen-, werk-, resultaat- en bestandidentiteiten. Na een echte Windows-herstart moeten dezelfde gegevens beschikbaar zijn, de gewone route weer werken en de oorspronkelijke effecten/resultaatzending niet opnieuw worden uitgevoerd. Alleen gezonde containers of een gewijzigd Linux-boot-ID zijn daarvoor onvoldoende.

De bestaande bootreader is bruikbaar voor de classificatie. De historische uitgeschakelde postbootservice is geen toegelaten nieuwe proef: haar oude `verify`-route bevat een modelcanary en verouderde runtimebindingen. Zij is niet opnieuw bewapend door deze review. De huidige voorherstartmeting vermeldt Windowsboot `2026-10-01T09:48:48.5000000Z`; een postherstartmeting ontbreekt nog.

F1.2 blijft dus open. De volledige eindvergelijking, fysieke spraak-/gezinsbeoordelingen en zeven dagen gezinspraktijk met Jaaps eindoordeel blijven afzonderlijk staan.

## Bewijsbinding van deze voorbereidende beoordeling

Private basis: `/home/administrator/project/.eva2-artifacts/f1-joint-check-20261003/`. Geen privé-inhoud is in dit reviewbestand overgenomen.

| Bestand | SHA-256 |
| --- | --- |
| `offline/run.py` | `1261fec32d7c3769996c1312b177c3724042b4ab397f820720be9ff16f3b4aed` |
| `offline/receipt.json` | `8a52c443169b6bf65821f47f3b6b2c315a61fd91d6cbca01c9097f9bfed9f180` |
| `offline/actual-image-schema-source.json` | `b07d245ec7e1d6b370520158331803ed014cf01ec391df018e9126c68a8d04cf` |
| `offline/fixture-adaptation.json` | `476d31061f9cdd015f60daddfa0996748bd91362fbccc739b6783871a56dbfa3` |
| `offline/return-mock-correction.json` | `742893fb9de4141ea7c6bd831abcd2c7f3fefbe4758dde0c87da320872b829d1` |
| `offline/return.log` | `f4585fdef1b9919cabb3b45425913998accbe3e54d4dd2b60e5f5f0ac3131331` |
| `offline/return-corrected.log` | `6acb4509916378a0ba370877a7e397892c2067f613aeb35c2d94d6b805c191a1` |
| `live/protocol.json` | `3cb3cdfaf36ea181b113532c07588479d575790ff74a734419ac350a4bb99f77` |
| `live/replies.private.json` | `3e82bf19fb643672a9449aab45ac69ea6e0631725282bfa09fc241e73022866e` |
| `live/journal.private.json` | `35d49e6778a937156e3fe3e31c2154efb63b246a4bad6fc8db484e177dc406ef` |
| `live/actual-documents.private.json` | `954bd8972c4be685e64535d90a1de52ffe35b567f1c05985cc69eb9cafcc58e3` |
| `live/boot-before.json` | `5c13f6836b4ef3dd81c0ca68523c1e1e7dbe633732155f71991ee2f795a474e0` |

## Aanvulling — concrete geheugen- en resultaatbinding vóór herstart

De eerder ontbrekende geheugenbinding is inmiddels geleverd en onafhankelijk gelezen. De gerichte uitlezing van het bestaande Hindsight-document bevat beide synthetische testwaarden, de oorspronkelijke bronmessage, metadata voor de juiste persoon en diens observatiescope. Het opgeslagen document heeft contenthash `f833e52fef608701d696adbcc6cb4bdccbefd478a041ff6ad85e2faa8aec65ea`. Dit is daadwerkelijk opgeslagen, persoonsgebonden geheugenbewijs; latere bruikbare herinnering na Windows-herstart is nog niet beproefd.

De gewone eigenaar-API voor werkresultaten retourneert beide exacte voltooide resultaten, met hun oorspronkelijke werk-/callidentiteit en outputhash. De API noemt de aflevering terecht `private_route_available` en zegt expliciet dat dit geen aflevering bevestigt. Het aanvankelijke lege retourhistoriebestand wordt daarom niet als bewijs van een productfout gebruikt. Het afzonderlijke browserbewijs voor daadwerkelijke presentatie en erkenning blijft op dit beoordelingsmoment nog te beoordelen.

De voorgestelde uitsluitend lezende herstartsnapshot bindt twintig bestaande containers, zeven systeemdiensten en vier gebruikersdiensten, de drie werkelijke hostclientbestanden, de oorspronkelijke taken/calls/resultaten, hetzelfde Hindsight-document en beide bestandsbytes. De reviewer heeft één meetcorrectie gemeld: `systemctl ExecStart` bevat ook veranderlijke procesmetadata, zodat de ongenormaliseerde waarde geen zuivere configuratiehash is. Die metadata moet afzonderlijk worden benoemd of vóór configuratievergelijking worden genormaliseerd; unit- en drop-inbestandhashes blijven de statische binding. Er is daarmee geen productwijziging of extra proef gevraagd.

| Aanvullend privébewijs | SHA-256 |
| --- | --- |
| `live/memory-document.private.json` | `dc11eab62f5fa7a2caf20a2db07c2833a19fdf56658be5df1a1d863565bd56a8` |
| `live/ordinary-results.private.json` | `0fd7e8f74ec69aec7641ffafb6f485b361be259ed1884f6d0fd98fdd90b20e43` |

**Besluit blijft: geen F1.2-eindacceptatie vóór echte Windows-herstart en controle daarna.** Deze aanvulling sluit de concrete voorherstartbinding van geheugen en opvraagbare taakresultaten; zij claimt nog geen live resultaatpresentatie of behoud na herstart.

## Aanvulling — voorbereiding voor echte Windows-herstart gereed

**Beperkt besluit: GO voor de vastgelegde voorbereiding; F1.2 blijft open tot de Windows-herstart en nacontrole.** De reviewer heeft uitsluitend de bestaande nieuwe bewijzen gelezen. Geen extra model-, provider- of browseraanvraag, herstart of wijziging aan diensten is hiervoor gedaan.

De gewone tokenvrije eigenaarpagina antwoordde HTTP 200. In een afzonderlijke Chromium-context waren beide oorspronkelijke taakresultaten daadwerkelijk zichtbaar in het DOM. Hun outputbytes en hashes matchen de opgeslagen resultaten. Na herladen leverde de normale geschiedenis opnieuw dezelfde resultaten. De bron van de browsercontrole is gelezen: zij gebruikt de bestaande pagina en haar JavaScript, injecteert geen resultaat en doet geen handmatige acknowledgement.

Beide resultaten waren echter al in de eerste geschiedenisresponse aanwezig. Deze controle bewijst daarom zichtbaarheid en opnieuw opvragen na herladen; zij bewijst niet dat die context de oorspronkelijke acknowledgements veroorzaakte. De duurzame tijdlijn bevat per resultaat één claim en één acknowledgement voor dezelfde operation-ID, met de expliciete aanduiding `offered_not_proven_heard`. De oorspronkelijke clientprovenance is onbekend. Er wordt geen menselijke ontvangst of werkelijk lezen geclaimd.

De definitieve vóór-herstartsnapshot bindt twintig bestaande containers, elf systeem-/gebruikersdiensten, drie actuele hostclientbestanden, dezelfde twee voltooide taken met claim count 1 en ongewijzigde call/resultaatidentiteit, hetzelfde Hindsight-document en beide testbestanden. Kern en werker hebben de eerder toegelaten imagepins en zijn gezond. Alle twintig containers draaiden; gezondheidsstatus wordt uitsluitend geclaimd waar een healthcheck bestaat. De elf diensten waren actief, inclusief de bestaande statische voice-ingressdienst en de firewalldienst met afgeronde oneshotstatus. Bestaande lifecycleverschillen, waaronder de twee beheerde browsercontainers zonder Docker-herstartbeleid, blijven zichtbaar en zijn niet stilzwijgend gewijzigd.

De snapshothelper sluit dynamische `ExecStart`-procesvelden nu uit van de configuratiedigest. Het commando blijft gebonden door de daadwerkelijke unit- en drop-inbestandhashes. Deze meetcorrectie is vóór de eerste definitieve snapshot aangebracht. De geheugenidentiteit en originele-teksthash matchen exact het eerder gecontroleerde document; beide bestandshashes en taakvelden matchen eveneens hun eerdere bewijs.

De resterende handeling is de eigenaar-geïnitieerde echte Windows-herstart, pas na afronding van de lopende afzonderlijke aanmelding. Daarna volgen de bestaande geplande bootclassificatie, controle van dezelfde runtime/configuratie/gegevens en gewone herinnering/resultaatopvraag zonder herhaling van de oorspronkelijke taak. Een proces- of WSL-herstart vervangt de gevraagde Windows-herstart niet. Er is geen toestemming voor een herstart gevraagd of gebruikt door deze reviewer.

| Definitief voorbereidingsbewijs | SHA-256 |
| --- | --- |
| `browser-return/receipt.private.json` | `4d1e4907521319eb8847063412feb000885b873b9c83f24d48d01a774faf37ff` |
| `browser-return/check.cjs` | `085eb995a4003e89e928b2b5ef23f69bf55efad0f709e7d780bc440b2ee0346f` |
| `live/before-reboot.private.json` | `82bdde24a8cc908eab30f87b269c07d2022b8a0ff5a44131a0277d87f4e16b2a` |
| `live/snapshot-reboot.py` | `0b451dbf7c073c9f2070ddbd26f3d75e766064b3c3f41f1e5ea051264e4a6bc6` |
