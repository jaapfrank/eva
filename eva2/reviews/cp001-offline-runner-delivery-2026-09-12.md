# CP-001 offline runner — begrensde oplevering, 12 september 2026

Status: geïmplementeerd en offline getest; onafhankelijke technische review open.
Geen livegang, modelcall, eigenaaracceptatie, productscore of concurrentiewinst.
CP-001's volledige matrix blijft open. Dit document is bewijs, geen werkbord.

Codecheckpoint: `9254c1a32a047d0a1e7340ebe62ba6f5885c8ee7`, parent `7affebbd`.
Branch: `codex/cp001-runner-20260912`.
Duurzame checkout: `/home/administrator/project/.eva2-artifacts/cp001-runner-20260912`.
De commit van deze notitie verandert de uitgevoerde code niet.

## Resultaat en concrete grens

De bestaande EV-002-validator krijgt geen verborgen uitvoermodus. De nieuwe
`benchmark/inspect_ai_v1/cp001.py` voert de vooraf vastgelegde twee taken, elk
tweemaal per deelnemer, uit als native Inspect-taak. Fixture, worker en scorer
zijn bronverzegeld; de worker krijgt geen target, rubric of deelnemerlabel.
Per beurt start een afzonderlijk proces. De voorkeurcorrectie blijft bewaard;
bij de notitietaak wordt het proces na schrijven maar vóór bevestiging beëindigd
en daarna hervat. De runner leest geheugen/bestanden en hashes apart terug.

Beide deelnemerlabels gebruiken uitdrukkelijk dezelfde vaste testdouble.
Daarom is dit een werkende uitvoer-/registratiedry-run, geen uitvoering van de
EVA2- of Bottie-productcode. De native multi-turn-/herstelroutes moeten nog
vergelijkbaar worden toegelaten. De bestaande managed-B single-promptprobe is
gelezen, maar niet gestart of ten onrechte geschikt verklaard. Er is geen
nieuwe productruntime, benchmarkengine, modeljury of native adapter verzonnen.

## Uitgevoerd bewijs

Exacte bestaande EV-001-lock: Inspect 0.3.263, OpenAI 3.6.0, 86 dependencies,
Python 3.12.3. Wheels geïnstalleerd met `--require-hashes --no-deps` uit de
bestaande lock; geen runtime-dependency gewijzigd. Installatielog en afgeleide
installatielijst staan in `/home/administrator/project/.eva2-artifacts/cp001-eval-env-20260912`.
Dat pad bevat ook de gebruikte interpreter en afzonderlijke `verify_log.py`.

- `pytest -q tests/benchmark/test_inspect_ai_ev001.py tests/benchmark/test_cp001_runner.py`:
  20 passed (13 bestaand, 7 nieuw). `ruff check` op de drie Pythonpaden: groen.
- CLI `--dry-run` op het genoemde checkpoint: acht native Inspect-samples,
  alle fixturechecks C, echte productuitslagen unknown, geen aggregatiescore.
- CLI `--fault unavailable`: acht admission_failed/unknown; geen workerstart.
- CLI `--fault timeout`: acht timeout/unknown; subprocess beëindigd en opgeruimd,
  geen herkansing, geverifieerd-eindtijd null.
- De lokale regressies toetsen daarnaast failure, input-/output-/fase-/tijdbudget,
  gemuteerde readbacks en dubbele mutatie, gelijke requesthashes/budgetten,
  schone starts, verkeerde commit en gewijzigde fixturebytes.
- Alle native logs zijn apart teruggelezen: acht samples, gelijke inputs en
  budgetten per paar, nul native modelgebruik. De runner verifieert de werkelijk
  geregistreerde uitvoervolgorde tegen de verzegelde afwisseling. Inspect sorteert
  samples in de logweergave op ID; `execution_index` bewaart de werkelijke volgorde.

Artifacts, elk buiten `/tmp`:

| Runmap onder `/home/administrator/project/.eva2-artifacts/` | SHA-256 native `.eval` |
|---|---|
| `cp001-final-dry-run-20260912` | `3d911b9881fcc9f9b972f92027ee1ba092089bd15affeb76fd47b6b5e40864b6` |
| `cp001-final-unavailable-20260912` | `2e8c25c128392d8b95b2da55074e24f364c84ad14e63381bfcb1a6e48b73ad76` |
| `cp001-final-timeout-20260912` | `386b2d92f4de733756521159f5f5e4785d187cf6661dc4b4f6931b93a8203b6d` |

Iedere map bevat een `receipt.json` dat naar het originele log verwijst en het
vooraf geschreven manifest bindt. Native samples bevatten gebruik, fase-uitvoer,
readbacks, artifacthashes en afzonderlijke latencyvelden. Menselijke rubricvelden
blijven null; fixture-modelgebruik is bewezen nul, geld is onbekend. Dit zijn
proefadaptertijden, geen vergelijkbare producttijden of geldige p95.
Eerdere ontwikkelruns blijven behouden maar zijn niet dit codecheckpoint.

## Review en integratie

De coördinator is om onafhankelijke review gevraagd. Controleer bronverzegeling,
identieke invoer/grenzen, echte uitvoervolgorde, lost-ack-hervatting, onafhankelijke
readback en onbekend-behandeling, inclusief de expliciete testdoublegrens.
De README bevat de uitvoerinstructies en officiële upstreambronnen, gecontroleerd
op 12 september. De bestaande Inspect-versie is behouden; geen actualiteitsclaim
over nieuwste releases.

Integratie: beoordeel uitsluitend deze codecommit plus deze bewijsnotitie;
geen blinde branchmerge, gedeelde indexmutatie of livewijziging. Terugweg is het
weglaten/terugdraaien van uitsluitend deze benchmarkbestanden. Centrale status
wordt alleen door de aangewezen admin bijgewerkt. Een vervolg kan de twee echte
native routes toelaten en aan dezelfde Inspect-taak verbinden; deze notitie
verleent daarvoor geen modelcall- of liveautorisatie.
