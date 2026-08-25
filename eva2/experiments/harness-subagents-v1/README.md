# Pydantic AI/Harness-subagents — twee kleine hergebruikproeven

Deze map bewijst dat Eva geen eigen modelrunner, contextisolatie,
request-/tokenbudget, time-out, annulering of interactieve subagentmotor hoeft
te bouwen. De exact vastgezette Pydantic AI `2.34.0` en Harness `0.24.0` leveren
die delen.

Er zijn bewust twee publieke routes. Harness voert de korte, door het
parentmodel gekozen `delegate_task` uit. Voor een taak die al duurzaam in
PostgreSQL is toegelaten gebruikt `tool_free_child.py` rechtstreeks Pydantic
AI `Agent.run()`: Harness `0.24.0` retourneert daar publiek alleen tekst en
maakt getypeerde output, eigen usage en cancellationstate niet beschikbaar.
De smalle adapter bewaart die gegevens wel zonder een tweede plannermodel.

Eva voegt in deze proef slechts haar eigen autoriteitsgrens toe. Het model en
de vaste workerinstructie komen uit één verzegeld operatorprofiel; alleen de
begrensde taaktekst komt uit de duurzame opdracht. Automatische agentbestanden,
parenttools, shared capabilities, recursie en child-netwerktools staan uit. De
vaste providerverbinding van de modeladapter is daarvan uitdrukkelijk
uitgezonderd: zonder die verbinding kan een extern model niet antwoorden.

De Pydantic AI-route gebruikt maximaal één modelrun en één voltooid antwoord.
Eva bindt daar een absolute deadline, provider-outputlimiet en verzegeld
prijsprofiel aan. De maximale kosten worden vóór de call tegen dat profiel
gereserveerd. Na succes bewaart Eva het hoogste van het profieltarief en de
eventuele Pydantic-kost, conservatief in hele USD-micro's. Onbekende gebruiksgegevens
heten `unmeasured` en nooit stil nul. Procesbrede Pydantic AI-telemetrie wordt
voor de privétaak expliciet uitgezet; vooraf geïnstrumenteerde en fallback-
modellen worden geweigerd.

De gecombineerde providerloze suite staat op 45 tests: de bestaande 22
Harnesschecks plus 23 checks voor het gesloten `DelegatedWork/v1`,
getypeerde JSON, contract- en outputhashes, verzegelde model-/workerprofielen,
absolute deadline, prijsreserve, provider-outputlimiet, eerlijke usage,
cancellation, telemetry-uitzetting, outputvalidatie en foutredactie. De
contractcode gebruikt Eva's bestaande Pydantic-basis onder `eva.contracts`;
er is geen eigen serializersysteem toegevoegd.

Dit is nog geen productpromotie. De eerste review vond echte fouten in deadline,
kosten, usage en pakketplaatsing; bovenstaande 23 tests zijn de lokale
correctie en wachten op onafhankelijke herreview. PostgreSQL-attention/handoff,
de eenmalige `call_started`-fence, cumulatieve crashbudgetten, echte
model-/prijsreleaseauthority, processandbox, Kanbanprojectie en een echte
provider-run staan open. Pydantic AI telt voltooide modelantwoorden, niet
betrouwbaar iedere transportpoging; Eva's toekomstige ledger moet daarom de
providercall vóór I/O afzonderlijk vastleggen. De Harness-time-out kan geen
blokkerende sync-code of extern effect terugdraaien. Daarom blijft deze child
privé, toolvrij, contextvrij, workspacevrij en effectvrij. De proef verandert B
niet en gebruikt geen secret of productdatabase; alleen de fixture draait en er
is geen echte providerverbinding gemaakt. De prijsreserve is dus alleen hard
tegen het gedeclareerde proefprofiel; een echte providerproef moet nog bewijzen
dat de releaseauthority de werkelijke providerprijs juist bindt.
