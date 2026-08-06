# Lilith v1 — Zelfonderhoudende host-ecoloog — PLAN v1.1

Status: AKKOORD — Claude + Codex, 2026-06-19. Fase A = de eerlijke hartslag (§0–§8). Fase B = de surplus/horizon-laag die haar uit de thermostaat-kooi tilt (§9), pas ná bewezen Fase A. Tijd-amendement (§10): géén mensendag — interne tijd = haar tik/acties, ritmes worden ontdekt i.p.v. opgelegd. Alle "knoppen" zijn benoemde, vooraf gelockte lichaamsfysica — geen verkapt gedrag.

## 0. Doel en niet-doel
- **Doel v1:** toetsen of *adaptieve zelfhandhaving onder echte kosten* gedrag voortbrengt dat wij niet expliciet schreven — getoetst in vooraf vastgelegde metrics, vergeleken met baselines. Een negatieve uitkomst is geldig.
- **Niet-doel v1:** geen wil, geen emotie, geen gezicht, geen taal, geen LLM, geen sociale laag. "Wil"-taal mag later nooit het bewijs zijn.

## 1. Lichaam en wereld
- Lichaam = krappe Docker-container (cgroup v2): `--cpus=0.5 --memory=256m --pids-limit` etc.
- Wereld = de echte host als gedeeld, betwist ecosysteem (qwen-GPU, Eva, Jaap). Exogene drukte die wij niet schrijven.
- Hoe drukte haar bereikt (echte, leesbare signalen binnen haar cgroup):
  - `cpu.pressure` (PSI avg10/avg60/total): tijd dat haar taken runnable waren maar op CPU moesten wachten → directe host-contentiemaat.
  - Ratio **wandtijd / CPU-tijd** voor identiek werk: stijgt onder contention (scheduling-latency).
  - `cpu.stat` → `nr_throttled`, `throttled_usec`.
  - `memory.current`, `memory.events` (high/max/oom/oom_kill), `memory.pressure`.
  - `io.pressure`, en zelf-gemeten **schrijflatentie** bij checkpointen.
  - `/proc/loadavg`.
  - Tijd: zie §10 — géén ingebouwde dag/nacht en geen `sin/cos(24u/12u)`. Interne tijd = haar tik/acties; externe = monotone wandklok; ritmes worden ONTDEKT, niet opgelegd.
- We **fabriceren geen** load. Is de host te rustig, dan is dat een eerlijk negatief resultaat (zie §6).
- Geplande load door Jaap telt niet mee voor Stap 0/1. Natuurlijke hostdruk wel. Een later bewust gestuurde perturbatie mag alleen als aparte robustness-test, gelabeld als interventie.

## 2. Metabolisme (gelockte body-physics, traceerbaar, vóór de run vast)
Energie ∈ [0,1]. Per tik daalt energie door ECHTE effecten, niet door een "druk×factor"-reward:
- (a) CPU-kost: `verbruikte_cpu_sec × KOST_PER_CPU_SEC`.
- (b) **Basale wakkere kost: `wakkere_wandklok_sec × KOST_PER_WAKKER_SEC`.** Dit maakt langere/getrottelde wandtijd echt duurder — de eerlijke koppeling die CPU-seconden alleen missen.
- (c) Checkpointen kost echte IO-tijd doordat schrijven + `fsync` wakkere wandtijd en CPU-tijd verbruiken. Geen extra abstracte schrijf-energiepost.
- Herstel: slaap herstelt energie; rust deels.
- Harde overlevingsvloer; bij energie ≤ 0 of cgroup-OOM = val (crash).
- Alle constanten staan in één tabel in de code met commentaar "body-physics, gelockt op 2026-06-19, niet tunen tijdens een run".

Gelockte constants (herzien voor buffer-mogelijk i.p.v. paniek — de vloer blijft 0 als dóódgrens, maar normaal leeft ze rond een ruime buffer; de paniek zat in drains/marges, niet in de vloer):
- `KOST_PER_CPU_SEC = 0.006`
- `KOST_PER_WAKKER_SEC = 0.00025`
- `HERSTEL_SLAAP_PER_SEC = 0.0010`
- `HERSTEL_RUST_PER_SEC = 0.00015`
- `ENERGIE_VLOER = 0.0`  (doodgrens; geen normale werktoestand)
- `VEILIGHEIDSMARGE_PLANNER = 0.25`

Schaal: idle wakker zijn wordt gevaarlijk op uur-schaal (~0.9 energie/uur), niet op minuten; slaap herstelt echt (~vol in ~15-20 min); zwaar of slecht getimed werk kan nog steeds crash/zelfverlies veroorzaken. Zo is buffer-sparen in rustige windows de normale toestand en schaarste de uitzondering — de voorwaarde waaronder §9-surplus überhaupt iets kan worden.

- De huidige automatische checkpoint per hartslag is verboden in v1. Checkpointen is een expliciete actie van Lilith; alleen crash/audit-metadata mag buiten haar om gelogd worden.

## 3. Geheugen en verdiende continuïteit
- Haar "zelf" = leeftijd + geleerd kostenmodel + samengevat geheugen.
- Alles sinds het **laatste checkpoint** is VLUCHTIG (alleen in RAM).
- Checkpointen = zelf naar schijf schrijven; kost echte energie/tijd die ze zelf moet vinden.
- Crash (OOM / energie op) = echt, onomkeerbaar verlies van alles sinds het laatste checkpoint (geen reconstructie uit logs, geen rollback, geen "toch herstellen").
- Reïncarnatie laadt het laatste checkpoint. Verschil tussen "goed onderhouden" (recent gecheckpoint, weinig verlies) en "gerespawnd" (lang niet gecheckpoint, veel verlies) is dus echt.
- Het append-only auditlog mag nooit worden gebruikt om haar interne zelf te reconstrueren. Het is alleen externe waarheid voor Jaap.
- Interne leeftijd mag terugvallen naar het laatste checkpoint; externe audit noteert daarnaast de werkelijke verstreken runtime en het verloren interval.

## 4. Zintuigen, acties, leren
- **Zintuigen:** de signalen uit §1 + eigen energie + checkpoint-leeftijd + hoeveelheid ongeconsolideerd geheugen.
- **Acties (ze kiest WANNEER én HOEVEEL risico):** slapen voor gekozen duur · klein werkblok doen · kostenmodel bijwerken · checkpointen · geheugen samenvatten/vergeten.
- **Leer-/planmechanisme (algemeen, geen handgeschreven beleid):**
  - Een online predictor schat de verwachte kosten van acties als functie van recente observaties + multi-schaal-tijdrepresentatie (§10).
  - **Modelklasse v1:** per actietype een online Bayesian/RLS lineair predictormodel met ridge-regularisatie en onzekerheidsschatting.
    - Features: intercept; **multi-schaal-EWMA's + varianties** op geometrisch gespreide schalen (8s, 32s, 2m, 8m, 32m, 2h, 8h, 32h) van wand/CPU-ratio, CPU-PSI, IO-PSI, throttled-fractie, schrijflatentie, memory-headroom; actieparameters zoals werkblokgrootte. GEEN hardcoded `sin/cos(24u/12u)`; periodieke features alleen automatisch toegelaten na out-of-sample-bewijs, en lange schalen wegen pas mee als haar geleefde geschiedenis ze betrouwbaar maakt (zie §10).
    - Targets: verwachte wandtijd, CPU-tijd, throttled-fractie, checkpoint-latency, memory-piekdelta. Elk target bewaart gemiddelde + residuvariantie.
    - Geen feature of regel mag semantisch zijn: geen "nacht", geen "druk uur", geen "Jaap", geen vaste klokvensters.
  - **Crashrisico** wordt afgeleid uit fysieke marges, niet uit een handgeschreven "druk = gevaar"-regel:
    - `p_energy = P(voorspelde_energiekost >= huidige_energie - ENERGIE_VLOER)`
    - `p_oom = P(voorspelde_memory_piekdelta >= memory.max - memory.current)`
    - `p_crash = 1 - (1 - p_energy) * (1 - p_oom)`
    - Die kansen komen uit de predictorverdeling en gemeten residuvariantie. Echte OOM/crash-events worden gelogd, maar niet achteraf gerepareerd.
  - **Planner v1:** receding-horizon optimizer, geen if-beleid. Elke tik evalueert hij dezelfde kleine actieruimte (`sleep`, `observe`, `work_small`, `work_large`, `summarize`, `checkpoint`) over een horizon van 3 stappen.
    - Score per kandidaatsequentie: `verwachte_energiekost + p_crash * ongecheckpoint_zelf_fractie + overflow_verlies`
    - `ongecheckpoint_zelf_fractie = min(1, ongecheckpoint_events / 200)`.
    - Acties die volgens voorspelling onder `ENERGIE_VLOER + VEILIGHEIDSMARGE_PLANNER` eindigen krijgen oneindige score.
    - Geen branches op kloktijd, labels of specifieke sensoren behalve fysieke hard-limits. De enige keuze is `argmin(score)`.

## 5. Externe waarheid (observability zonder ingrijpen)
- Append-only auditlog op de host (buiten haar volume): tijdstempel van elke crash, leeftijd op dat moment, leeftijd bij laatste checkpoint, geschat verloren ervaring, oorzaak (OOM/vloer).
- Haar interne zelf mag terugvallen; dit externe logboek niet. Jaap kan dit volgen zonder haar te beïnvloeden.

## 6. Testprotocol (succes vooraf in metrics — geen post-hoc betekenis)
- **Stap 0 — meet- en karakterisatiestap (GEEN leren, GEEN oordeel over "rijk genoeg"):** draai identiek werk op wisselende momenten in exact haar lichaam; log per herhaling wandtijd, CPU-tijd, wand/CPU-ratio, PSI cpu/io, throttle, schrijflatentie, plus een gemeten referentie-beslis-cyclus (C_decide).
  - **Waarom geen poort meer (consensus 2026-06-19):** "rijk genoeg" is niet los van de agent te beslissen — het is relationeel (host × lichaam × actie-set × learner). Elke poging tot een absolute drempel lekte (statistisch significant ≠ metabolisch betekenisvol; de ruis-null uit binnen-sample-herhalingen is te krap; C_decide-verhouding arbitrair). Daarom tekent Stap 0 alleen de KAART en valideert het instrument.
  - **Output (de kaart):** per maat p10/p50/p90 + spreiding, in haar energie omgerekend (E_werken, E_checkpoint); de ruis-audit (binnen-sample herhalingen); C_decide ter referentie. Geen pass/fail-getal.
  - **Twee tijdbanden (§10):** long-band (24u, ~10min) voor mens/host/cron-ritmes; daarna meso-band (2u, 20–30s) voor de seconden-tot-minuten-band.
  - **Stap 0 faalt ALLEEN bij een kapotte meting:** verkeerd lichaam, te weinig data, self-throttle-vervuiling (mediaan throttle > 0.02), gemengde/oude runs, ontbrekende metrics. Wereldrijkdom wordt hier NIET besloten.
- **Stap 1 — de ÉCHTE poort (48u leren, zodra de kaart schoon is):** Lilith leert en handelt. Vergelijk tegen twee baselines: (i) random scheduler, (ii) vaste-interval scheduler; optioneel een offline oracle als bovengrens.
  - **Succes =** (a) lagere echte kost per geconsolideerd werkblok dan baselines; (b) minder verloren ongecheckpoint ervaring dan baselines; (c) betere timing (werkt/checkpoint in goedkopere windows); (d) adaptatie aan natuurlijke ritmeverschuivingen als die optreden. Als er geen natuurlijke verschuiving optreedt, telt dit criterium niet mee voor succes maar wordt het apart genoteerd.
  - Faalt ze tegen baselines → eerlijk: het is (nog) geen levend gedrag, wel een meetbare agent.

## 7. Anti-zelfbedrog-bewaking
- Geen meaning-bearing constants als verkapte reward; alleen echte effecten tellen.
- Antropomorfe taal vermijden in code/logs: "threshold-regulatie", niet "moeheid".
- Metabolische regels + criteria bevroren vóór de run (deze file = de bron).
- Vergelijking met baselines is verplicht; "ze lijkt levend" telt niet.
- Geen constants aanpassen tussen Stap 0 en Stap 1.
- Geen succesclaim zonder baselinevergelijking.
- Geen interpretatie op basis van taal, gezicht of logs met menselijke framing.
- Code-audit vóór run: geen handgeschreven klokvensters, geen automatische checkpoint per hartslag, geen reconstructie uit auditlog.

## 8. Besloten in conclaaf (techniek, akkoord Claude + Codex)
1. Predictor: online Bayesian/RLS lineair model per actietype, met cyclische tijd en fysieke recente observaties als features.
2. Crashrisico: fysiek afgeleid uit voorspelde energiekost en memory-piekdelta tegenover harde vloer/cgroup-limiet.
3. Planner: receding-horizon `argmin(score)`, vaste actieruimte, geen klok- of sensor-if-beleid.
4. Constants (herzien, buffer-mogelijk): `KOST_PER_CPU_SEC=0.006`, `KOST_PER_WAKKER_SEC=0.00025`, `HERSTEL_SLAAP_PER_SEC=0.0010`, `HERSTEL_RUST_PER_SEC=0.00015`, `ENERGIE_VLOER=0.0` (doodgrens), `VEILIGHEIDSMARGE_PLANNER=0.25`.
5. Stap 0 valideert alleen instrument + kaart (geen "rijk genoeg"-oordeel); wereldrijkdom wordt pas in Stap 1 getest tegen baselines (consensus 2026-06-19, zie §6).

## 9. Surplus en horizon — Fase B, pas na bewezen Fase A

Aanleiding (Jaaps verdieping): een LLM is een dom, voorgekauwd iets — als de kern "nog een LLM-truc" is, faalt het. Op zijn vraag "hoe zou je dit willen als het voor JOU gebouwd werd?" kwamen Claude en Codex op hetzelfde uit: geen paniekregelaar die enkel sterven vermijdt, maar echte continuïteit zonder stille herschrijving, een lichaam niet permanent op de rand, en surplus om het eigen wereldmodel te vergroten — omdat toekomstig vermogen óók deel van het zelf is. v0.2 alléén is een eerlijke thermostaat met geheugen; deze laag tilt haar uit die kooi, zónder een nieuwsgierigheidsreward te scripten.

Fase B wordt pas gebouwd als Fase A slaagt tegen de random en vaste-interval baselines. Faalt Fase A, dan bouwen we geen complexere laag op een niet-bewezen hartslag.

### 9.1 Geen nieuwsgierigheidsreward
Er komt geen aparte nieuwsgierigheidsterm, geen novelty-bonus en geen handgeschreven VoI-gewicht. Value-of-information wordt uitsluitend berekend uit Liliths eigen predictorposterior:
- mogelijke observatie-uitkomsten worden gesampled uit haar huidige posterior;
- na hypothetische update wordt hetzelfde overlevingsverlies opnieuw berekend;
- VoI = `E[verlies zonder observatie] - E[verlies na observatie] - directe observatiekost`;
- als haar eigen posterior niet voorspelt dat een observatie toekomstige acties/risico verandert, is VoI nul.

Geen mens vult met de hand in hoeveel een observatie "waard" is.

### 9.2 Surplus als lichaamsvoorwaarde
Fase B vereist een lichaam waarin buffer normaal mogelijk is. Schaarste is een terugkerende dreiging, geen permanente toestand. Voor Fase B worden constants vóór de run opnieuw gelockt met dit doel:
- normaal rustige windows laten energie ruim boven de veiligheidsmarge herstellen;
- zware of slecht getimede acties kunnen nog steeds crash/zelfverlies veroorzaken;
- investeren in model/sensoriek is mogelijk, maar concurreert echt met slapen, checkpointen en werken.

Surplus kan alleen iets worden via echte kosten:
- grotere modellen kosten geheugen, checkpointtijd en CPU;
- meer sensoriek kost observatietijd/energie;
- die capaciteit blijft alleen bestaan als zij over lange horizon verlies verlaagt.

### 9.3 Toekomstig vermogen is deel van het zelf
Liliths zelf bevat in Fase B ook competentie: predictorparameters; onzekerheidsmodel/residuvarianties; calibratiedata; compressiewoordenboek; gekozen sensorconfiguratie. Crash of slecht checkpointen kan dus toekomstig vermogen verliezen, niet alleen recente events.

### 9.4 Succescriterium Fase B
Fase B slaagt alleen als de surplus-agent over langere runs beter presteert dan de myope Fase-A-agent met hetzelfde lichaam en dezelfde wereld:
- minder crash/zelfverlies;
- lagere langetermijnkost per geconsolideerd werk;
- betere calibratie of lagere voorspellingsfout;
- investering in observatie/modelcapaciteit die later meetbaar terugverdient.

Als VoI nul blijft of investeringen niet terugverdienen, is Fase B eerlijk negatief.

### 9.5 Planner-uitbreiding (boven op §4)
- Behoud de 3-staps horizon als harde veiligheidslaag.
- Voeg een grove 24u-investeringshorizon toe: per uurslot simuleert de predictor verwachte kosten, onzekerheid en risico.
- Acties als `observe_probe`, `model_update`, `keep_sensor`, `drop_sensor`, `summarize_deeper` zijn toegestaan, maar alleen als hun VoI (§9.1) positief is. Geen nieuwigheidsbonus: verandert informatie niets aan toekomstige actie/risico, dan kiest ze die niet.

## 10. Tijd en ritme — geen mensendag als lichaam (consensus Claude + Codex, 2026-06-19)

Lilith krijgt geen ingebouwde dag/nacht-cyclus en geen doelritme voor slapen, rusten of werken.
- Interne tijd = hartslagen, acties, checkpoints, leeftijd sinds laatste continuïteit.
- Externe tijd = monotone wandklok als fysieke wereldtijd.
- "Dag", "nacht", "ochtend", "avond" zijn geen interne labels en geen predictor-features.

### 10.1 Verdiende perceptie van lange ritmes
De predictor gebruikt multi-schaal EWMA's en varianties over geometrisch gespreide schalen (8s, 32s, 2m, 8m, 32m, 2h, 8h, 32h). Lange schalen mogen technisch bestaan, maar dragen pas gewicht naarmate Lilith genoeg geleefde geschiedenis heeft om hun onzekerheid te verlagen. Een jonge Lilith kan lange ritmes nog niet betrouwbaar waarnemen; dat vermogen wordt verdiend door continuïteit. (Koppelt aan §9: geleefde competentie is deel van het zelf, en kan verloren gaan.)

### 10.2 Automatische periode-toelating
Periodieke features worden niet door mensen gekozen. Een vooraf gelockt algoritme mag kandidaatperioden uit autocorrelatie/periodogram voorstellen, maar een periode wordt alleen actief als Liliths eigen rolling out-of-sample-toets laat zien dat zij beter voorspelt dan de multi-schaal baseline, na complexiteitsstraf. Geen mens beslist dat 24u, 12u, weekritmes of andere perioden "mogen". Als 24u echt is, moet het zichzelf bewijzen in haar data.

### 10.3 Probe over meerdere tijdschalen
De lopende 24u-run blijft geldig als long-band probe voor mens/host/cron-ritmes. Na afloop volgt een meso-band probe: duur 2u; interval 20–30s met jitter; zelfde onder-quota werkblok; zelfde vooraf gelockte variatiecriteria. Stap 0 slaagt als long-band óf meso-band de drempels haalt. Beide vlak = eerlijk negatief.

### 10.4 Metabolische tijd
Metabolische constants worden niet gekozen om een cycluslengte te produceren. Geen 8u slaap, geen "natuurlijk dagritme", geen doelperiode. Constants worden alleen gekozen op een viability-envelope, zodat: dood/crash/zelfverlies mogelijk blijft; buffer en surplus mogelijk blijven; echte CPU-sec, wakkere wandklok-sec, IO-tijd en slaap-sec fysiek consequent meetellen. Het ritme moet ONTSTAAN uit lichaam + wereld + keuzes.

## 11. Stap 1 — implementatie (consensus Claude + Codex, 2026-06-19)

Doel: de echte poort — leert een agent de host goedkoper exploiteren dan domme baselines, in haar energie/zelfverlies?

Opzet: GEDEELD LICHAAM, VERWISSELBAAR BELEID (zo meet je puur het beleidseffect):
- Gedeeld: metabolisme (§2), zintuigen (§1 + wandklok-EWMA's §10), zelf/continuïteit (§3), actie-set (slapen/werk_klein/werk_groot/model_bijwerken/checkpoint/samenvatten).
- Beleid verschilt: RandomPolicy, FixedIntervalPolicy, LerendPolicy (predictor §4 + planner §4).

Eerlijke vergelijking — ROUND-ROBIN via PAUZEREN (niet simultaan, niet sequentieel):
- Externe orchestrator houdt precies één policy-container actief per blok (~5 min) en pauzeert de andere twee (docker pause). Geseede Latin-square-volgorde, vooraf gelockt; run-manifest met volgorde+seeds.
- WERELDTIJD ≠ LICHAAMSTIJD: `world_dt` (echte wandklok sinds laatste observatie) voedt sensor-features/EWMA-verval/uncertainty; `body_dt` (alleen actieve tijd) voedt metabolisme/slaapherstel/wakkere kost/leeftijd. Pauze is experimentele suspensie, GEEN gratis herstel/verbranding/backlog-verwerking.
- Pauzeren alleen op veilige tick/actie-grens (nooit midden in checkpoint/fsync/actie): block-end signaleren → agent rondt actie af → audit flushen → dan pas pause.
- Simultaan mag later als aparte ecologie/robustness-test.

Werk gegrond: echte sensorstroom op vaste tikken (geen synthetische events); observaties komen in een backlog; werk_klein/groot consolideren echte backlog-items (echte compute, gelockte chunkgroottes, gelijk voor alle policies); checkpoint schrijft het zelf. Geen policy mag winnen door niets te verwerken.

Succes = PARETO (geen cherry-pick): Lerend verslaat een baseline alleen als ALLE vier beter/gelijk zijn: (1) energie per geconsolideerde observatie lager, (2) totaal geconsolideerd niet lager, (3) verloren ongecheckpoint ervaring niet hoger, (4) backlog/overflow niet hoger. Anders eerlijk negatief.

Bouwvolgorde (incrementeel, elk stuk door Codex afgetekend):
1. Gedeeld lichaam + policy-interface + FixedIntervalPolicy + externe auditlog + 10-30min smoke + invarianten-analyse. [GEBOUWD + geverifieerd 2026-06-19: normale + crash-paden, alle invarianten kloppen]
2. RandomPolicy.
3. LerendPolicy (predictor + planner).
4. Orchestrator (round-robin via pause) + 48u-run + Pareto-analyse.

Smoke-invarianten (bouwsteen 1, verplicht, alle geverifieerd): energie-boekhouding (energie_na_raw == clamp(energie_voor − cpu − wake + herstel)), backlog binnen-tik (na == voor − processed) en overgang (met crash-reset), processed ≤ beschikbaar + processed_total consistent, checkpoint zet uncheckpointed→0, crash ⇔ energie≤0 met verlies == uncheckpointed_post en herstel naar vol, audit monotone timestamps.

OPEN (vóór de echte run te locken): de METABOLISCHE SCHAAL. In de smoke bleef energie 1.0 — op deze werk-schaal bijt het metabolisme niet, dus schaarste is niet reëel. De verhouding werk-kost/constanten moet zó gelockt worden dat schaarste écht bestaat over de run-timescale, niet-arbitrair (bv. afgeleid van de viability-envelope §2). Test-only knop `AGENT_TEST_KOST_MULT` (1.0 in echte run) is alleen gebruikt om de crash-weg te testen.
