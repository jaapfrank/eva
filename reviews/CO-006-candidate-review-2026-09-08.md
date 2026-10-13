# CO-006 onafhankelijke kandidaatbeoordeling — 2026-09-08

**Oordeel: GO voor begrensde ingebruikname van de geheugenreparatie via de bestaande quiescent A/B-route, slot 4 naar slot 3.** Geen algemene productacceptatie en geen goedkeuring om browserbevoegdheden uit te breiden. Review uitgevoerd door `/root/co006_review`, onafhankelijk van bronbouwer en testuitvoerder; alleen bron/evidence gelezen en hashes gecontroleerd. Geen tests herhaald, productcode gewijzigd of runtime aangestuurd.

## Exacte binding

- Implementatiecommit: `05f9da50ca7b6566c0669ca75805c53be52d9ff6`; diff tegen zijn ouder bevat de geheugenadapter, regressies en twee vernieuwde release-indexen.
- Kandidaatbron: `0588068410e272fd2d8b0a7d120b7419336dee0c`.
- Image: `sha256:6a22970534900a71c0c7a9ac23f029f4fb924fcb36330607591f22141aff6f03`.
- `src/eva/adapters/projection/hindsight.py`: `22b41699bbb5fd1bb72d49fbf57b57793b37e67871fa6cf4fa1d3eb2e953a20d`.
- `tests/projection/test_hindsight.py`: `7ca3a8f665e33d624d861709774d97628628f21ec03805a190942e5885965337`.
- Required-tests receipt: `721a695f0271c0e627d054a52edf79f9b70c935061e9e11db62026fa9936c170`.
- Admission: `49ba4709d850fb5c6fd9a37cfa35de67631a7b71cc272ed1df378058c310e1fe`.
- Reserveplan: `15b97c050a26e43b0300d35ab0279f90c201f6e9c18511e261e5ce560da31f25`.
- `prepare_slot.py`: `4f53c9abdcff48d92a5edee26466a4ee50e2193e1ec689da310ea46405dfe673`.
- `cutover.py`: `6300e3e5547eb6111d5442d8ea5d1166e13acdcf277e35ac9b6e8a92b619ed39`.
- Bestaande cutoveroperator: `5935268199be06f8777ee3d18509c9b8cca6d2d11053c49a4c6fb710a012b097`.

Bronhashes zijn rechtstreeks uit de kandidaatcommit berekend. Alle twaalf `originalFiles` uit admission zijn opnieuw gehasht en komen overeen, inclusief image-archief, installed-image tests, scan, copied-data receipt en browserreceipt. Originele bestanden staan onder `/tmp/eva2-co006-memory.JxnTze` (scan-databasemetadata op het afzonderlijk vermelde pad); de productevidence staat in `evidence/product/CO-006-autonomous-memory-2026-09-08.json`.

## Inhoudelijk oordeel

De foutreparatie is beperkt en passend: de retain operation identity omvat nu de volledige canonieke bronrevisie en bank, zodat gewijzigde inhoud of scope niet het succes van een eerdere documentbewerking hergebruikt. Identieke retries blijven deterministisch, ook met een verse adapter. Na voltooide retain vervalt de betreffende warme persoonscache en voorkomt de generatiecontrole dat een oudere lopende recall die terugplaatst. De globale generatie kan ook lopende verversingen voor andere personen laten vervallen; hun bestaande warme cache blijft staan. Dat is conservatieve cachewerking, geen vermenging van personen.

Het bewijs bevat vijf eerst falende regressies, daarna 52 passing adaptertests, dezelfde 52 met de gewijzigde module uit het image, en een synthetische broncorrectie met exacte readback en persoonsisolatie. De volledige verplichte run heeft 1.984 passing tests. Alleen de fysieke browsersubgroep van die brede run voert het kandidaatimage uit; andere groepen gebruiken verzegelde bron/wheelhouse-inputs. Dit onderscheid blijft expliciet. De copied-data proef ondersteunt herstart/replay, maar haar gespreksantwoord is geen zuivere Hindsight-recallproef.

## Browserafwijking blijft open

De eerste brede poging liep vast bij browser-testcollectie na 60 seconden. Een volgende poging gaf 3 pass / 1 fail (`ambiguous` waar `provider_accepted` verwacht werd). Daarna slaagden dezelfde vier gevallen in een diagnostische run en opnieuw in de volledige run met oorspronkelijke argumenten. **De oorzaak van deze intermittente afwijking is niet vastgesteld; twee passing runs herstellen die fout niet.** Het kandidaatbewijs bewijst succesvolle uitvoeringen, geen betrouwbare foutvrije browserwerking.

Dit blokkeert een algemene claim over browserbetrouwbaarheid. Het blokkeert deze beperkte geheugenrelease niet: de wijziging raakt retain-identiteit/cache, verandert geen browseruitvoering, schema, dependency, model of bevoegdheid, en de bestaande fail-closed/ambiguity-afhandeling wordt niet verruimd. Heroptredende browserambiguïteit moet als onopgelost resultaat zichtbaar blijven, zonder automatische herhaling van mogelijk uitgevoerde effecten. De afwijking moet als apart vervolgwerk behouden blijven.

## Bereik en voorwaarden voor uitvoering

Het gelezen plan wijzigt op het gestopte reserveslot uitsluitend image-, release- en configuratiehash. Het vergelijkt identiteit, budget, persoonsbindingen en tokenbestanden op bytegelijkheid en schrijft geen credentials. De wrapper bindt voorbereiding en uitvoering aan exacte invoerhashes. De bestaande operator stopt ingress en schrijvers, kopieert actuele databases met attestaties, controleert de kandidaat vóór routering en houdt de uitgaande credential tijdens staging achter. Bij falen probeert hij terugkeer inclusief nieuwe targetwrites; bij dubbel falen blijven herstelbestanden bewaard.

GO geldt uitsluitend zolang die ongewijzigde wrappercontroles slagen en het actuele bron-slot nog overeenkomt. Mismatch, falende readiness, ontbrekende databaseattestatie of falend herstel is een stopconditie; niet omzeilen. Deze review vervangt geen werkelijk cutoverreceipt of live readback. Geen nieuwe handmatige Telegram- of huisactie is voor deze beoordeling uitgevoerd.

Niet bewezen: zeven dagen gezinspraktijk, volledige semantische geheugenkwa­liteit, afhandeling van 34 historische `needs_help`-jobs, automatische verwijdering van alle verouderde gegevens, nieuwe initiatiefvaardigheid of winst op concurrenten. CO-006 mag pas als live worden beschreven na geslaagde uitvoering; gebruikersacceptatie blijft afzonderlijk.

## Aanvulling na ingebruikname — 2026-09-08

**GO voor technische oplevering van de begrensde CO-006-geheugencapaciteit; gebouwd, getest en live zijn onderbouwd. Menselijke acceptatie blijft open.** Gelezen: daadwerkelijke cutoverreceipt, live-replayreceipt en de replayprobe; geen tests of liveoproepen door deze reviewer herhaald.

- `rollout/cutover.json`: `34eaaa7cbec3b340bdbcad1e57be04ba15fbb4f64db684eb72e63bd387b9ed81`. Status `switched`, targetpoort 44710 ready, bronwriters gestopt, vijf bestaande Eva1-routes HTTP 200. De receipt bindt de synchronisatie van 48 operationele en 23 geheugentabellen aan schema-, inhoud-, count- en dumphashes. De bestaande gecontroleerde operator geeft deze receipt pas na restore-attestatie en readiness af. De actuele operatie heeft geen rollback hoeven uitvoeren; dit is geen nieuwe live-terugrolproef.
- `rollout/live-replay.json`: `244b6bb34ee4328593cefe0026b816e58d6a3192f59843cc5570e2e3f94de29e`. De probe controleert daadwerkelijk image-id, draaiende container, poort en readiness en herhaalt een bestaande ontwikkelaarsturn via de private client. Antwoord en opgeslagen meetgegevens blijven exact gelijk. Dit bewijst replay na cutover, geen nieuw gegenereerd geheugenantwoord, menselijke Telegramrondgang of nieuw initiatief. De nulcalls/nulsends gelden voor deze probe, niet voor alle achtergrondactiviteit.

De checkerwijziging voegt uitsluitend `memoryreviewer: /root/co006_review` toe aan TEAM. CO-006 gebruikt die reviewerRole. Bestaande historische rollen, waaronder `/root/identity_pair_review`, blijven onveranderd. Deze toeschrijving is correct en versoepelt de onafhankelijke reviewvoorwaarde niet.

De huidige ruime `doneWhen`-zin over geheugenonderhoud in gewone gesprekken mag niet gelezen worden als bewezen algemene gezinskwaliteit. De aantoonbare technische grens is: bestaande Hindsight-selectie van voorkeur, gewijzigde voorkeur, tijdelijke afspraak en irrelevante herhaling is begrensd met synthetische gesprekken onderzocht; broncorrectie/retry/cachegedrag is gerepareerd en getest; persoonsgrenzen, gekopieerde-dataherstart en de actuele A/B-ingebruikname/replay zijn onderbouwd. Algemene geheugengezondheid, historische hersteljobs en acceptatie in gewone gezinsgesprekken blijven open. Houd deze grens zichtbaar bij technische afsluiting in het dashboard; de eerdere browserbeperking blijft gelden.

## Definitieve evidencecommitbinding

Deze review bindt aan evidencecommit `2e135d60bfc79db948f0470f350706880dd0f252`, waarvan de rechtstreeks gecontroleerde ouder implementatiecommit `05f9da50ca7b6566c0669ca75805c53be52d9ff6` is. Het GO sluit uitsluitend de beperkte technische geheugenbronreparatie af. De oorspronkelijke bredere gebruikersuitkomst, autonoom geheugen in gewone gezinsgesprekken met langetermijnkwaliteit en menselijke acceptatie, blijft expliciet open onder CO-008. Die scheiding bewaart het oorspronkelijke doel en maakt van technisch releasebewijs geen gebruikersacceptatie.
