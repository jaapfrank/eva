# Eva 2 tegenover OpenClaw, Hermes en Agent Zero

Bijgewerkt: 24 augustus 2026.

## Eerlijke conclusie

- De eerdere vergelijking was **niet volledig**.
- OpenClaw `2026.8.1`, Hermes `0.20.5` en Agent Zero `2.10` waren wel actueel,
  echt uitgevoerd en op broncommit vastgezet.
- Alleen C01, T02 en C04 zijn op alle vier uitgevoerd. Dat meet een klein deel
  van gesprek, terughoudendheid en stijl.
- Eva's 15 core-live- en 9 native-receipts zijn niet als dezelfde volledige
  reeks op de drie concurrenten uitgevoerd.
- Hun skills, plugins, gateways, nodes, kanalen, browser, desktop, MCP,
  planners, schedulers en multi-agentfuncties waren niet volledig geconfigureerd
  of vergeleken.
- De bestaande cijfers blijven geldig voor die drie gevallen, maar bewijzen
  niet dat Eva 2 als compleet product beter is.

## Productstrategie v1: beter waar het telt, hergebruiken waar dat slimmer is

Eva hoeft niet ieder oppervlak zelf te evenaren. V1 is bewust **Telegram-first**:
één betrouwbare gezinsroute is waardevoller dan tien half-afgemaakte kanalen.
De documentatie van de concurrenten is voldoende voor deze
capability-inventaris; zij is geen bewijs dat hun functie in onze opstelling
werkt. Daarom testen we niet hun hele product opnieuw, maar uitsluitend Eva's
eigen beloften die de gebruiker werkelijk merkt of die een effect in huis kunnen
hebben.

| Keuze | Eva-doel en vergelijkbare uitkomst | Hoe jij het vergelijkt | Huidige stand |
|---|---|---|---|
| **Zelf beter maken** | Menselijk, consequent gesprek met meerbeurts begrip; afzonderlijk en gedeeld geheugen voor gezinsleden; geen verzonnen actie; toolketens met readback; hervatten zonder dubbel effect; begrensde self-improvement. | Eén echte Telegram-B-proef per eindcase met bericht-, event-, tool- en readbackreceipt. Jij ziet antwoord en gedrag; de onafhankelijke reviewer controleert de keten. | Nog pre-freeze: multi-persoonidentiteit, eindimage en MX01-MX06. |
| **Standaard hergebruiken** | MCP, `SKILL.md`, ClawHub, OpenClaw Gateway/nodes en eventueel browser/desktop blijven vervangbare capability-providers. Eva houdt identiteit, Hindsight, policy, ledger en eindantwoord. | Vergelijk de providerfunctie met Eva's receipt: Eva mag dezelfde capability gebruiken, maar moet haar veilige doelbinding en bewijs behouden. | Ontwerp gereed; eerste read-only sidecar nog open. |
| **Bewust niet v1** | Veel kanalen, eigen gateway, companion-nodevloot, voice/media, Canvas/desktop en brede providerroutering. | Geen vergelijking nodig voordat Telegram + kern betrouwbaar zijn. Een later kanaal moet dezelfde identity-, receipt- en consentcontracten volgen. | Niet blokkend voor v1. WhatsApp is alleen een toekomstige connectorinterface. |

De vier-framework gate blijft alleen een bescherming tegen een algemene claim
als “Eva is overal beter”. Zolang hij rood is, doen we die claim niet. Hij is
geen voorwaarde om Eva v1 op Telegram af te maken.

## V1-afvinkbord — kleine opleverstukken

Elk open vak is één klein stuk werk met eigen Git-checkpoint, gerichte test en
onafhankelijke review. Een vak wordt pas afgevinkt als die drie er zijn; een
langdurige verzamelaanpassing telt niet als voortgang.

- [x] Concurrentiedocumentatie vertaald naar `zelf beter`, `hergebruik` en
  `niet v1`; Telegram is de enige verplichte v1-route.
- [x] Eindnorm vastgelegd: bestaande core 15/15 en native 9/9 blijven intact;
  MX01-MX06 dekken multi-persoon, meerbeurts gesprek, toolketen, crashherstel
  en begrensde self-improvement.
- [x] Draaiende B-v53 broninhoud hashgebonden vastgelegd zonder secrets te
  lezen of B te wijzigen.
- [x] V53-bronherstel opgesplitst in Git: migratie-input, tijdlijnmodule en
  adapterlaag zijn onafhankelijk gecontroleerde baseline-checkpoints.
- [x] V53-applicationlaag als eigen baseline-checkpoint hersteld: 20
  containerhashes, compile en 11 gerichte tests onafhankelijk groen.
- [x] V53-runtime- en operationslaag als eigen baseline-checkpoint hersteld:
  16 containerbronnen, compile en vier Telegram-tests onafhankelijk groen.
- [x] V53-Hindsight-fixture gesloten: de ontbrekende `PATCH`-mock is als
  vijf-regelig testcheckpoint hersteld; drie tests en onafhankelijke review
  zijn groen.
- [x] Hindsight hot/cold-budget: de kandidaat-runtime configureert het
  recallniveau, 2.000–4.000 contexttokens en 20–100 retrieval-nodes
  fail-closed voor Hindsight. Dezelfde nodegrens geldt voor low/mid/high en de
  geconfigureerde nachtelijke reflectietrigger gebruikt dezelfde tokenlimiet;
  onafhankelijke review, 27 gerichte checks en een schone non-root
  image-import zijn groen (`d6bc705`). B is niet gedeployd.
- [x] Managed-B Hindsight-preflight: optionele hot/cold-limieten worden al
  vóór een Compose-render met precies dezelfde grenzen geweigerd; ontbrekende
  waarden houden de verzegelde manifestdefaults. 37 gerichte checks en
  onafhankelijke review zijn groen (`cf3df2a`). Dit is geen B-adoptie.
- [x] V53-kandidaatimage is schoon gebouwd uit de herstelde branch en importeert
  de runtime in een nieuwe container. Hij is niet gedeployd en vervangt B niet.
- [x] Git-only runtime-importclosure: de Docker-build uit een schone Git-archive
  (dus zonder de 117 dirty worktreepaden) importeert `runtime` en de Telegram-
  ingress in een niet-root, read-only kandidaatcontainer (`68c66ed`). Dit is
  geen startup-, Hindsight-, PostgreSQL- of B-adoptieproof.
- [x] V53-bronintegratiekandidaat is op een schone branch hersteld met behoud
  van de bestaande secret- en Telegram-hardening: 22 gerichte tests, compile
  en een niet-root image-smoke zijn onafhankelijk groen. De kandidaat is nog
  niet geforceerd in de huidige dirty worktree gemerged.
- [x] De bestaande ongetrackte recoverybron is ongewijzigd als Git-baseline
  vastgelegd (`2fc19f3`): 107 bron-, migratie-, test- en deploymentbestanden,
  met onafhankelijke review, compile en 23 gerichte tests; zie
  `reviews/recovery-source-baseline-2fc19f3.md`. De kandidaatpatches blijven
  afzonderlijk totdat ze gericht met deze baseline zijn verzoend.
- [x] 4D-authoritybron is als één exact recoverycheckpoint vastgelegd
  (`84153b0`): schedule, connector-, profiel- en model-fixtures, drie gates,
  checker en freeze-test wijzen weer naar schedule `f492…`. Onafhankelijke
  review bevestigt dat dit geen schema-reseal claimt. De checker stopt op de
  huidige 66-schema-registry (`3369…`) versus de historische 64-schema-
  authority (`fcf…`).
- [x] Afzonderlijke 4D-64-schema-kandidaat (`5fafab8` op historische basis
  `5f72d59`) is statisch, positief/fail-closed en met één disposable
  PostgreSQL-geheugen/herstarttest gecontroleerd. Dit is geen volledige
  4D-gate, B-adoptie of product-liveclaim; zie
  `reviews/4d-64-schema-candidate-5fafab8.md`.
- [x] Buildinput B01: kandidaatcontext komt uitsluitend uit een expliciete
  vaste Git-commit, niet uit de vuile werkmap. Dockerfile, lockfile,
  COPY-inputs, archiefhash en commit-tijd worden secretvrij gebonden; een
  imagebuild, netwerk, B-adoptie en releaseclaim zijn bewust nog uitgesloten.
- [x] Wheelhouse B02a: de toekomstige offline amd64/CPython-3.12-wheelhouse
  is nu exact gebonden aan de 38 hash-gecontroleerde metadatarecords en de
  lockfile. De validator weigert ontbrekende, extra, source-, symlink- en
  gewijzigde bestanden zonder iets te installeren of te downloaden. De
  fysieke wheel-bytes en de offline imagebuild blijven bewust afzonderlijk.
- [x] Wheelhouse B02b: een exact gesloten lokale artifactmap kan nu naar een
  nieuwe private, read-only wheelhouse worden gekopieerd met hashcontrole vóór,
  tijdens en na de kopie. Herkomst uit een goedgekeurde artifactproxy is nog
  niet bewezen; er is niets geïnstalleerd, gebouwd of aan B gekoppeld.
- [x] Offline buildregel B03a: de kandidaat-Dockerfile accepteert alleen een
  externe `wheelhouse` en installeert uitsluitend met `--no-index`, hashes en
  binary-only. De Git-bronarchive mag die map juist niet bevatten; B03a bouwt
  nog geen image en bindt nog geen fysieke wheelhouse aan een buildcontext.
- [x] Offline preflight B03b: B01-bronarchive en B02b-read-only wheelhouse
  moeten byte-voor-byte dezelfde lock delen; archive, Dockerfile, basisdigest,
  materialization-receipt en beide tree-hashes worden opnieuw gecontroleerd.
  De proef gebruikte tijdelijke lokale bytes, zonder Docker of B.
- [x] OCI-basisinput B03c: een lokale, read-only OCI-export is nu exact aan de
  gepinde bronindex en `linux/amd64` gebonden; hash, manifest, config en lagen
  worden gecontroleerd. Een geldige maar andere OCI-keten wordt geweigerd.
  Dit blijft een lokale export, geen registry-provenance of releaseclaim.
- [x] Offline buildcontext B03d: B01-bron en uitsluitend de exact verzegelde
  B02b-wheels worden in één nieuwe private, alleen-lezen Docker-context gezet;
  B03c blijft buiten de context en is alleen via de receipt gebonden. Een
  onafhankelijke review reproduceerde en sloot de extra-wheel-race. 23 gerichte
  tests en een tijdelijke fysieke materialisatie van 544 bestanden zijn groen,
  zonder netwerk of B.
- [x] Offline kandidaatimage B03e: de verzegelde context bouwt nu expliciet
  als `linux/amd64`, met uitgeschakeld netwerk, zonder pull/cache/tag/push. De
  actuele lokale basisimage moet vooraf exact bij B03c passen; de ongetagde
  kandidaat wordt daarna teruggecontroleerd. 27 gerichte tests, fysieke build
  en onafhankelijke review zijn groen; B is niet gestart of vervangen.
- [x] Kandidaat-runtime-smoke B03f: de ongetagde kandidaat start tijdelijk
  local-only (`--pull=never`), netwerkloos, read-only en als niet-root zonder
  mounts of secrets; het verpakte runtimepad importeert en de container wordt
  automatisch opgeruimd. Gerichte tests en onafhankelijke review zijn groen.
- [ ] V53-buildclosure: de offline kandidaatimage is gebouwd, maar de
  versiepassende eindtestscope en beheerde startupconfiguratie ontbreken nog.
  De 4D-64-schema-kandidaat is afzonderlijk bewezen; de huidige hoofdregistry
  met 66 schema's mag echter niet stilzwijgend als een nieuwe 4D-freeze gelden.
  De versiepassende kandidaatset en resterende tests moeten nog worden
  vastgezet.
- [x] Telegram-ingress: DSN, token en principal-bindings gebruiken nu dezelfde
  harde fileloader; manifest + 15 tests + onafhankelijke review zijn groen.
- [x] Core-database-DSN: de runtime weigert een inline DSN, inclusief een lege
  waarde, en leest uitsluitend een private DSN-file; 20 gerichte checks en
  onafhankelijke review zijn groen (`532d853`). Dit verandert B niet.
- [x] Telegram-coresecret: ook de runtime weigert een inline bot-token,
  inclusief een lege waarde, en leest uitsluitend een private tokenfile;
  15 gerichte checks en onafhankelijke review zijn groen (`a68e868`). Stripe
  blijft een afzonderlijke open secretconsumer.
- [x] Stripe-coresecrets: de runtime weigert inline API- en webhooksecrets,
  inclusief lege waarden, en behoudt uitsluitend private file-routes; 19
  gerichte checks en onafhankelijke review zijn groen (`d825a79`). Dit is geen
  Stripe-provider- of financiële liveproef.
- [x] MCP C01: één getypeerde, no-secret `streamable_http`-tool is strikt
  beperkt tot `read`, één vaste resource, 1–5 seconden en hashgebonden
  JSON-schema's. De vaste adapter doorloopt `initialize`, `initialized`,
  `tools/list` en precies één `tools/call`; JSON en SSE, schema-afwijking,
  ongeldige output en een langzaam druppelende respons worden lokaal echt
  beproefd en fail-closed geweigerd. Twee onafhankelijke reviews zijn groen.
  Sidecar, Eva-ledger en B-proef blijven afzonderlijk open.
- [x] Duurzame-takenworker: DSN alleen via private file-secret; manifest + 18
  gerichte tests + onafhankelijke review zijn groen. Nog niet als live B-worker
  gestart; dat blijft onderdeel van het beheerde manifest en de eindproef.
- [x] Schema-installer: DSN alleen via private file-secret; apart non-root
  profiel + 14 gerichte tests + onafhankelijke review zijn groen; zie
  `reviews/schema-installer-file-secret-9e129a4.md`. De oude stackmigratie
  blijft bewust geen beheerd B-releasepad.
- [x] Modellease-consumer: core leest alleen een atomair gedeelde lease met
  vast `1000:65532`/`0640`-contract; 45 gerichte tests + onafhankelijke review
  zijn groen; zie `reviews/model-lease-consumer-5303622.md`. De leasepublisher
  en host-directorycontract blijven open.
- [x] Modellease-publisher: private authoritybron, vooraf-geprovisioneerde
  lease-directory en atomische publicatie zijn afgedwongen; 21 tests +
  onafhankelijke herreview zijn groen; zie
  `reviews/model-lease-publisher-3af02bb.md`. Hostprovisioning/B-deploy blijft
  open.
- [x] Managed-B-hostcontract: workspacevolume en Codex-, Eva- en Hindsight-
  directories zijn exact gepind en fail-closed gevalideerd; 20 tests +
  onafhankelijke review zijn groen; zie
  `reviews/managed-b-host-contract-ce23545.md`.
- [x] Managed-B-configuratievoorbeeld: alle verplichte preflightwaarden staan
  nu in één secretvrij template, inclusief het Telegram-koppelsecret; een test
  bindt het template rechtstreeks aan de actuele vereiste sleutelset én aan de
  exacte bytes van het managed manifest (`16f1519`, `d1ae331`). Dit is geen
  configuratie van of wijziging aan B.
- [x] PostgreSQL-bootstrap: gepinde upstream-image leest een direct gebonden
  wachtwoordbestand; een echte disposable proef bewijst verse init én behoud
  van bestaande data bij herstart; 24 gerichte tests + onafhankelijke review
  zijn groen; zie `reviews/postgres-password-file-contract-0c31872.md`. De
  zelfstandige manifest/adoptie blijft open.
- [x] Managed-B-manifestskelet: één gecontroleerde acht-service Telegram-route
  zonder Docker-secrets, met dezelfde labels, geheime file-binds en
  capabilitybehoud; onafhankelijke review is groen; zie
  `reviews/managed-b-manifest-skeleton-505caf8.md`. Hostbestands- en
  Hindsight-volumeverzegeling, adoptie en B-liveproef blijven open.
- [x] Managed-B-render: exact B-configuratie- en manifesthash worden
  fail-closed aan één alleen-lezen Compose-render gebonden; 39 gerichte tests
  en onafhankelijke review zijn groen; zie
  `reviews/managed-b-sealed-render-80f722c.md`. Dit start of vervangt B niet.
- [x] Managed-B-rendercommando: een operator kan de verzegelde render nu via
  één fail-closed, secretvrije receipt uitvoeren; 42 gerichte checks en
  onafhankelijke review zijn groen; zie
  `reviews/managed-b-render-command-80eb350.md`. Een echte B-configuratie en
  adoptierun blijven afzonderlijk open.
- [x] Managed-B-servicelabels: de toekomstige startcontrole vereist exact alle
  acht manifestservices met dezelfde configuratiesnapshothash; 41 checks en
  onafhankelijke review zijn groen; zie
  `reviews/managed-b-all-service-labels-6e8cae5.md`.
- [x] Managed-B-schema-profiel: een verse, geïsoleerde PostgreSQL doorloopt
  de echte schema-installer tot `4e_0001_operational_timeline`; de
  driftcontrole is groen en de onafhankelijke reviewer heeft alle tijdelijke
  Docker-resources verwijderd gezien. B is niet benaderd; zie
  `reviews/schema-installer-disposable-live-03ec03a.md`.
- [x] Managed-B-steady-observatie: zeven permanente containers worden na start
  alleen-lezen op project, labels en dezelfde configuratiesnapshot gecontroleerd;
  47 checks en onafhankelijke review zijn groen; zie
  `reviews/managed-b-steady-observation-0351852.md`.
- [x] Managed-B-runtimebestanden: HA, database-DSN en beide Telegrambestanden
  worden metadata-only en fail-closed gecontroleerd vóór een adoptierun; 24
  tests + onafhankelijke review zijn groen; zie
  `reviews/managed-b-runtime-secret-preflight-6ead73f.md`. PostgreSQL-
  wachtwoordbestand, Hindsight-volume en B-adoptie blijven apart open.
- [x] Hindsight-volume: de gekozen naam is verplichte, geldige en verzegelde
  B-configuratie; 25 tests + onafhankelijke review zijn groen; zie
  `reviews/managed-b-hindsight-volume-preflight-128e187.md`. Bestaande data,
  back-up en adoptie zijn bewust nog niet geclaimd.
- [x] PostgreSQL-hostsecret: de toekomstige root-owned `0600`
  wachtwoordfile wordt metadata-only en fail-closed gecontroleerd; 29 tests +
  onafhankelijke review zijn groen; zie
  `reviews/managed-b-postgres-secret-preflight-6157aa8.md`. B is niet
  benaderd; de uiteindelijke hostprovisioning/adoptie blijft open.
- [x] B-volumepins: PostgreSQL en workspace zijn read-only aangetroffen en nu
  exact gepind zonder data te lezen; onafhankelijke review is groen; zie
  `reviews/managed-b-observed-volume-pins-4ef9ae3.md`. De Hindsight-keuze
  wordt pas na routing- en back-upreadback in de adoptierun vastgezet.
- [x] B-herstelset: PostgreSQL-, workspace- en Hindsight-volumes zijn
  read-only naar privé, hash-gecontroleerde archieven gekopieerd; onafhankelijke
  live review is groen; zie `reviews/managed-b-readonly-backup-2026-08-24.md`.
  Dit is geen adoptie of B-release.
- [x] Telegram-identiteitsbasis: één persoon mag meerdere unieke Telegram-
  accounts koppelen zonder dat één account naar twee personen wijst; 7 tests +
  onafhankelijke review zijn groen; zie
  `reviews/telegram-multi-account-identity-38f4f6c.md`. De live
  multi-accountproef blijft open.
- [x] Telegram-koppelprimitief: een nieuw privéaccount krijgt een kortlevende,
  na herstart reproduceerbare code; alleen een bestaand gekoppeld privéaccount
  kan die bevestigen. De code staat niet plaintext in de tijdlijn en een
  bestaande accountbinding kan niet worden overschreven; zie
  `reviews/telegram-pairing-primitive-47a799d.md`. De worker gebruikt die
  route nu lokaal, via haar normale delivery-ledger en een beheerd file-secret
  (`07ed428`); alleen de echte Telegramproef blijft open.
- [x] Telegram-groepsconsent: alleen een eigen gekoppeld privéaccount kan voor
  een vooraf gebonden groep delen starten of stoppen. Zonder actieve toestemming
  bereikt een groepsbericht Eva's kern niet. Grant, stop en reject staan
  append-only in PostgreSQL; herhaling van hetzelfde Telegram-update-id verlengt
  niets. 55 gerichte tests, drie wegwerp-PostgreSQL-proeven en onafhankelijke
  review zijn groen (`c026a2c`). Dit is nog niet op B gedeployd en bewijst nog
  geen echte groepstest.
- [x] Groepsgeheugen A01: de pure admissionlaag maakt privé- en
  Telegram-groepspubliek verplicht verschillend. Een groepsbijdrage vereist
  een echte booleaanse toestemming én een structureel aan dezelfde persoon en
  groep gebonden consentreferentie; er bestaat geen groeps-naar-privéfallback.
  13 contractgevallen, bestaande ingress/consenttests en onafhankelijke review
  zijn groen. Retention/recall, Hindsight-tags en runtimewiring volgen apart.
- [x] Groepsgeheugen A02: alleen de actieve, duurzame PostgreSQL-consentstore
  levert nu een persoon- en groepgebonden consentreferentie uit haar laatste
  timeline-event. Ontbrekende, verlopen, ingetrokken of kruisende consent
  levert fail-closed geen referentie. Een echte PostgreSQL-16-proef op een
  tijdelijk `tmpfs`-volume, gerichte tests en onafhankelijke review zijn groen;
  retention/recall of B-runtime is nog niet gewijzigd.
- [x] Groepsgeheugen A03a: audience is nu verplicht en duurzaam van de
  interne API via het geheugendocument naar de PostgreSQL-outbox. Een oud
  Telegram-groepsrecord zonder audience faalt; een groepspubliek met auteur,
  groep en consentreferentie herstelt exact na een echte PostgreSQL-16-herstart.
  Groepsretentie wordt vóór Hindsight en vóór persoonlijke stijlreflectie
  gestopt. 63 gerichte checks, vier PostgreSQL-outboxproeven en onafhankelijke
  review zijn groen. Hindsight-groepstags en consentgebonden recall volgen in
  A03b.
- [x] Groepsgeheugen A03b0: iedere Hindsight- en compatibiliteitsrecall krijgt
  nu een verplicht audience. Tot de eigen groepstags bestaan, stopt een
  groepsaudience vóór bankkeuze, Hindsight-request, stijlspiegeling en
  SQL-fallback. 65 gerichte checks en onafhankelijke review zijn groen;
  A03b maakt hierna de afzonderlijke consentgebonden groepstags en recall.
- [x] Groepsgeheugen A03b1: Hindsight-retentie voor een groep gebruikt alleen
  een aparte groepstag, nooit een persoonstag of stijlreflectie. Vóór partitie,
  bank en HTTP-call moeten actuele consent, groep, conversatie-, document-,
  bron- en bewijsreferentie exact samenvallen; zo kan een foto-observatie nooit
  in groepsretentie komen. Private retentie/recall raakt de consentresolver
  niet. 21 Hindsight-tests en onafhankelijke review zijn groen; A03b2 voegt
  de bijpassende groepstag-recall en directe uitsluiting na intrekking toe.
- [x] Groepsgeheugen A03b2: Hindsight-recall gebruikt voor een groep uitsluitend
  de groepstag en nooit een persoonstag, project-/fotopartitie, SQL-fallback of
  stijlspiegeling. Caller en iedere herinnerde auteur worden opnieuw tegen de
  actuele duurzame consent gecontroleerd; intrekking sluit de herinnering vóór
  Eva's context uit. Groep, conversatie, bron en bewijs moeten exact kloppen.
  92 bredere geheugenchecks en onafhankelijke review zijn groen. A03c sluit de
  connectorneutrale turn- en Telegram-runtimewiring.
- [x] Groepsgeheugen A03c0: de connectorneutrale `ConversationTurn` vereist nu
  zelf een expliciet audience; recall en retentie ontvangen exact hetzelfde
  object. Groepsturns vereisen canonieke groep-ID's en blokkeren project- en
  foto-observaties. De generieke turn-API en huidige Telegram-HTTP-adapter
  weigeren groepen totdat A03c1 de vertrouwde connectoradmission levert. De
  private route blijft expliciet en ongewijzigd. 77 gerichte tests en
  onafhankelijke review zijn groen.
- [x] Dashboard D01: gesaneerde read-only runtime-statusroute, met expliciete
  secret-nonleaktest, `no-store`, onafhankelijke review en lokale read-only
  pagina (`a395cd7`). Authenticatie en B-deploy blijven open.
- [x] Dashboard D02-basis: een IdentityPack wordt niet direct gewijzigd; een
  gesloten staged draft is exact aan pack-id, versie en hash gebonden en geeft
  uitsluitend een reproduceerbare presentatiediff. Geen dashboard-write,
  publicatie, runtime-switch of B-wijziging (`91b2760`).
- [x] Dashboard D02a: de lokale alleen-lezen pagina toont nu uitsluitend de
  ondertekende én werkelijk `current` IdentityPack (naam, stijl, versie en
  pack-hash). Een staged/revoked pack of verificatiefout geeft een gesaneerde
  503; 17 gerichte tests en onafhankelijke review zijn groen. Geen draft-write,
  publicatie, runtime-switch of B-wijziging.
- [x] Dashboard D02b: een dashboardclient kan een gesloten staged
  IdentityPack-draft tegen precies de geverifieerde actuele pack-previewen.
  Alleen de deterministische presentatiediff komt terug; de draft wordt niet
  opgeslagen, gepubliceerd of als runtime-identiteit gebruikt. 19 gerichte
  tests en onafhankelijke review zijn groen. Een echte editor, signer,
  audit-event en rollback blijven afzonderlijke slices.
- [x] IdentityPack-verificatie: de runtime laadt alleen canonieke bytes van
  de gebonden huidige identiteit, met gecontroleerde bronhash en Ed25519-
  handtekening tegen een expliciete publieke trustbinding. Geen signer,
  publicatie, API-route of B-wijziging (`f586298`).
- [x] Managed-B-secretgrens: alle acht Telegram-v1-services lezen hun
  runtimegevoelige gegevens uitsluitend via private file-secrets of de vaste
  modellease; broninventaris en onafhankelijke review vonden geen resterende
  inline-secretconsumer. De feitelijke B-adoptie/start blijft afzonderlijk
  open. AgentMail en Stripe zijn latere, eigen capabilitygates.
- [ ] Telegram-groepsgeheugen sluiten: de geverifieerde multi-accountbindings,
  wederzijdse koppeling en groepsconsent zijn geïmplementeerd en gericht
  getest; admission, duurzame consentreferentie en veilige outbox-propagatie
  zijn eveneens klaar; generieke recall weigert een groep nu fail-closed. Wat
  nog ontbreekt is A03c1: de vertrouwde Telegram-admission en runtime-injectie
  met end-to-end non-leakagebewijs; de echte
  multi-account- en Telegram-groepproef blijft daarna afzonderlijk open.
- [ ] Eén echte B-Telegramproef met meerdere personen en groep uitvoeren. Dit
  vereist twee door de eigenaar geautoriseerde testaccounts en een testgroep.
- [ ] Toolketen, fysieke readback en crash midden in de keten op hetzelfde
  Telegram-B-pad bewijzen.
- [ ] Gateway/browser B01: Eva kan een site in een geïsoleerde browser zien,
  documentatie volgen en een onboarding of formulier voorbereiden. Een account
  maken, voorwaarden accepteren, betalen, KYC afronden of een financiële actie
  uitvoeren kan zelfstandig binnen een vooraf in de Eva-chat vastgelegd
  mandaat; Eva waarschuwt, vat de gevolgen samen en bewaart het mandaat,
  bewijs en een directe stopknop in haar tijdlijn. Buiten dat mandaat vraagt
  zij opnieuw om toestemming.
- [ ] Externe-accounttool E01: Eva kan na toestemming een dienstadapter
  opleveren, credentials als beheerd file-secret installeren en de verbinding
  read-only bewijzen. Het mandaat (budget, looptijd, markt, order- en
  verlieslimiet, stilzetten) wordt als vraag-en-antwoordgesprek in Eva
  vastgelegd; alleen de geheime sleutel gaat via een eenmalige veilige invoer.
  Bitvavo krijgt eerst alleen view/trade, IP-whitelist, geen opnamebevoegdheid
  en paper-/limiettests vóór echte orders.
- [ ] Eén eindimage bevriezen; core 15/15, native 9/9 en MX01-MX06 daarop
  werkelijk uitvoeren.
- [ ] Zevendaagse resident-run en onafhankelijke promotiebeslissing.

## Volledige productmatrix

`Bewezen` betekent live getest. `Aanwezig` betekent gedocumenteerde
productfunctie die nog niet in onze live vergelijking is getest.

| Onderdeel | Eva 2 nu | OpenClaw | Hermes | Agent Zero | Besluit |
|---|---|---|---|---|---|
| Natuurlijk gesprek en eigen karakter | C01, T02 en C04 live; gebruiker accepteert de ervaring nog niet | Dezelfde drie live | Dezelfde drie live | Dezelfde drie live | Eva's eigen meerbeurts Telegram-eindproef uitvoeren; geen algemene vergelijking claimen |
| Persoons- en projectisolatie | C02 live via geïsoleerde contractprovider; M01 gebruikte een lokale Telegram Bot API-contractprovider, geen Telegramnetwerk; echte Telegrambinding nog open | Aanwezig via gateway-routing en geïsoleerde sessies; niet door ons getest | Aanwezig via profielen/gateway; niet door ons getest | Aanwezig via projecten/profielen; niet door ons getest | Dezelfde private canaries en een echte cross-channelproef op alle vier |
| Langetermijngeheugen en stijl | PostgreSQL + Hindsight live; hot/cold-path gescheiden | Geheugenadapters aanwezig; niet vergeleken | Geheugen en sessiezoeking aanwezig; niet vergeleken | Doorzoekbaar en bewerkbaar geheugen aanwezig; niet vergeleken | Zelfde correctie-, verouderings-, privacy- en herstartproeven |
| Duurzame doelen en crashherstel | A01-A03, X01-X04 en F03-F04 deels/live bewezen; eindimage moet regressie nog krijgen | Gateway- en deliveryherstel aanwezig; niet gelijkwaardig getest | Cron, taken en deliveryledger aanwezig; niet gelijkwaardig getest | Taken/scheduling en workspacegeschiedenis aanwezig; niet gelijkwaardig getest | Fault injection op alle vier: crash vóór/na effect, geen duplicaat en eerlijke eindstatus |
| Home Assistant en fysieke verificatie | Beste bewijs in onze huidige configuratie: actuele staat, historie, acties en automatiseringen met readback | Integratie/skills aanwezig; vorige vergelijking had geen HA-configuratie | Eigen Home Assistant-toolset aanwezig; vorige vergelijking had geen HA-configuratie | Via MCP/API/plugin mogelijk; vorige vergelijking had geen HA-configuratie | Alle vier dezelfde afgeschermde HA-doelen geven; succes telt alleen na echte readback |
| Proactieve huisgenoot en nuttig zwijgen | In aanbouw; residentpolicy nog niet toegelaten | Heartbeats/hooks/automation aanwezig; residentgedrag niet getest | Gateway/cron/routines aanwezig; residentgedrag niet getest | Scheduled tasks aanwezig; residentgedrag niet getest | Dezelfde zevendaagse resident-track en spam-/stiltegates voor alle vier |
| Skillsmarktplaats | Nog geen veilige importer | ClawHub aanwezig | Skills Hub aanwezig | Skills en Plugin Hub aanwezig | Niet namaken: veilige importlaag en waar nodig een geïsoleerde sidecar gebruiken |
| Plugins en connectorbereik | Enkele eigen connectoren; geen volwassen marktplaats | Breed plugin- en kanaalecosysteem aanwezig | Skills, toolsets en MCP-integraties aanwezig | Gedocumenteerde community-Plugin Hub, custom tools, MCP en A2A; omvang niet door ons vastgepind of getest | Concurrentintegraties hergebruiken; alleen Eva's policy, geheugen en waarheid blijven leidend |
| Gateway en chatkanalen | Connectorneutrale kern, Telegram/Web/AgentMail deels; geen brede gateway | Sterkste huidige surface: Gateway plus veel kanalen | Gateway voor 15+ platforms aanwezig | Web/API/CLI-connectors aanwezig; kanaalbreedte nader vaststellen | OpenClaw/Hermes als transportprovider onderzoeken; nooit twee breinen of twee eigenaren van één kanaal |
| Companion nodes en apparaten | Niet aanwezig | Camera, scherm, locatie, voice, notificaties en hostcommands aanwezig | Geen gelijkwaardige nodeclaim door ons bewezen | A0 CLI geeft hostterminal, bestanden en hostbrowser; desktop/browser ingebouwd | OpenClaw-nodes via officiële Gateway-sidecar hergebruiken; A0 CLI alleen vergelijken, niet in Eva opnemen zonder voordeel |
| Browser en computergebruik | Websearch en begrensde bestanden; interactieve gateway/browser B01 staat gepland | Browser/tools en nodes aanwezig | Browser/terminal/code-toolsets aanwezig | Sterkste zichtbare surface: browser, hostbrowser en Linuxdesktop | Eén geïsoleerde provider achter Eva's ledger; accounts, contracten, betalingen en financiële stappen kunnen binnen een vastgelegd mandaat zelfstandig verlopen |
| MCP en standaard toolservers | Eén vaste, no-secret read-only Streamable-HTTP-tool werkt tegen een echte lokale JSON/SSE-provider; geen sidecar, ledger of B-route | MCP/node-hosted tools aanwezig | MCP met toolfilters aanwezig | MCP-configuratie aanwezig | De volgende kleine stap is één gepinde sidecar achter Eva's ledger; geen losse nieuwe API-connectoren |
| Multi-agentwerk | Ontwikkelproces gebruikt onafhankelijke agents; Eva-runtime heeft nog geen bewezen algemene delegatie | Multi-agent routing aanwezig | Delegatie/multi-agent aanwezig | Subagents en A2A aanwezig | Dezelfde onderzoek-, bouw-, review- en hervattingsopdracht op alle vier testen |
| Modelproviders en fallback | Eén gecontroleerde modelauthorityroute | Meerdere providers/routing aanwezig | Sterke providerselectie en fallback aanwezig | Modelpresets en meerdere providers aanwezig | Providerrouter alleen toevoegen als die Eva's receipts, budget en identiteit niet omzeilt |
| API, IDE en programmatische bediening | Interne HTTP-API; geen volledige externe SDK/IDE-gate | Gateway-RPC aanwezig | HTTP/API- en ACP/IDE-integraties aanwezig | HTTP/WebSocket en A0 CLI aanwezig | Dezelfde externe clienttaak, reconnect en permissiontest uitvoeren |
| Voice, spraak en media | Nog geen complete live multimodale producttrack | Voice/media en companion nodes aanwezig | Vision, TTS, voice en media-toolsets aanwezig | Speechplugins, attachments, browser en desktop aanwezig | Afzonderlijke privacy- en latencytrack met echte apparaten en media |
| Webextractie, UI, Canvas en artifacts | Bronzoeking en heropenbare Markdown live; geen volwaardige Canvas | Web/search/media en Control UI aanwezig | Webextractie en desktop/command surfaces aanwezig | Browser Canvas, desktop en documentsurface aanwezig | Echte research-, screenshot-, formulier- en documenttaken vergelijken |
| RL, batch en evaluatie | LAB-36 en receipts; geen algemene RL-toolset | Evaluatieoppervlak nader inventariseren | RL-toolset en overige batch/evalfuncties aanwezig; niet getest | Evaluatie- en workflowplugins nader inventariseren | Als aparte productcategorie testen of expliciet motiveren waarom niet nodig |
| Controleerbaarheid en bewijs | PostgreSQL-eventtijdlijn en readback zijn Eva's ontwerpsterkte | Gateway-events/logs aanwezig | Sessies, taken en deliverydata aanwezig | Live UI/logs, projecten en workspace time travel aanwezig | Eén gedeeld receipt-schema gebruiken; product-UI telt niet als bewijs van echt extern effect |
| Beveiliging en permissies | Fail-closed ontwerp, maar B-secretmigratie nog open | Scopes, pairing, sandbox en approvals aanwezig | Toolsets, sandboxbackends en approvals aanwezig | Projectsecrets, connectorpermissions en Dockerisolatie aanwezig | Gezamenlijke aanvalstests; communitycode altijd pinnen, scannen, isoleren en afzonderlijk toelaten |
| Geld verdienen / externe accounts | Alleen lokale commerce-simulatie; E01 definieert veilige account-onboarding, nog geen opbrengst | Via plugins/tools mogelijk, niet door ons getest | Via tools/MCP mogelijk, niet door ons getest | Via browser/plugins/MCP mogelijk, niet door ons getest | Eerst testaccount en least-privilege credentials; live financieel effect kan daarna zelfstandig binnen een chatvastgelegd budget-, tijd-, markt- en verliesmandaat met readback en stopknop |

## Wat Eva mag hergebruiken

### ClawHub

- Eva kan `SKILL.md`-bundels lezen en naar haar eigen tools mappen.
- Installatie gaat eerst naar quarantaine en wordt vastgezet op uitgever, exacte
  versie en digest.
- Een skill krijgt nooit automatisch toegang tot Eva's database, Hindsight,
  gezinsgegevens of secrets.
- Codeplugins draaien niet in Eva's Pythonproces.
- Pak alleen reguliere bestanden uit onder een nieuw leeg quarantaineroot;
  weiger absolute paden, `..`, symlinks, hardlinks, devices, sockets en FIFO's.
- Begrens gecomprimeerde én uitgepakte bytes, aantallen bestanden,
  compressieverhouding, padlengte en uitpaktijd om archiefbommen te blokkeren.
- Voer packagehooks, dependency-installers en meegeleverde scripts nooit uit
  tijdens discovery of unpack.

### OpenClaw Gateway en companion nodes

- Een gepinde OpenClaw Gateway mag als vervangbare capability-sidecar draaien.
- Eva vraagt de effectieve tools op en roept alleen vooraf toegelaten tools aan.
- Camera, scherm, locatie, voice en hostcommands blijven aan officiële,
  gekoppelde OpenClaw-nodes hangen.
- Eva schrijft vóór de call `TOOL_PREPARED`, gebruikt een idempotency key en
  controleert daarna zelf de echte doeltoestand vóór `STATE_VERIFIED`.
- OpenClaw krijgt geen eigen Eva-persona, gezinsgeheugen of autonome
  antwoordroute.

### Hermes en Agent Zero

- Eerst hergebruiken wat standaard is: `SKILL.md`, MCP en begrensde RPC/API's.
- Hermes' skills/toolsets en Agent Zero's plugins/browser/desktop worden alleen
  via een afzonderlijk proces gebruikt wanneer dat aantoonbaar sneller of beter
  is dan een directe standaardadapter.
- Geen framework wordt in Eva's hot-path een tweede orchestrator.

## V1-acceptatievolgorde

1. Houd de officiële, gepinde documentatie-inventaris bij en markeer iedere
   concurrentfunctie als `zelf beter`, `hergebruik` of `niet v1`.
2. Bouw eerst Eva's Telegram-kern af: meerdere geverifieerde personen,
   privé/gedeeld groepsgeheugen met toestemming, Hindsight-recall en een menselijk
   meerbeurts gesprek.
3. Bouw daarna toolketens, fysieke readback, crashherstel en begrensde
   self-improvement af.
4. Bevries één image en voer core 15/15, native 9/9 en MX01-MX06 live uit via
   echt Telegram en afgeschermde echte providers.
5. Hergebruik pas daarna één read-only capability-pad (MCP of Gateway/ClawHub)
   achter Eva's bestaande ledger. Extra kanalen, camera, locatie, voice en
   economische acties zijn aparte latere besluiten.
6. Een onafhankelijke agent controleert ontwerp, Git-scope, live-receipts en
   nonclaims. Synthetische tests blijven regressietests, niet eindbewijs.

## Primaire bronnen

- OpenClaw integraties: <https://openclaw.ai/integrations>
- OpenClaw Gateway-protocol: <https://docs.openclaw.ai/gateway/protocol>
- OpenClaw nodes: <https://docs.openclaw.ai/nodes>
- OpenClaw plugins: <https://docs.openclaw.ai/plugins>
- ClawHub-formaat: <https://github.com/openclaw/clawhub/blob/main/docs/skill-format.md>
- ClawHub-audits: <https://docs.openclaw.ai/clawhub/security-audits>
- Gepinde Hermes-bron uit de vergelijking: <https://github.com/NousResearch/hermes-agent/tree/f293e7206b4ddd66042329442c6afebc19a8808d>
- Hermes integraties: <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/integrations/index.md>
- Hermes tools: <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/user-guide/features/tools.md>
- Hermes MCP: <https://github.com/NousResearch/hermes-agent/blob/main/website/docs/guides/use-mcp-with-hermes.md>
- Agent Zero documentatie: <https://github.com/agent0ai/agent-zero/blob/main/docs/README.md>
- Agent Zero MCP: <https://github.com/agent0ai/agent-zero/blob/main/docs/guides/mcp-setup.md>
- Agent Zero browser: <https://github.com/agent0ai/agent-zero/blob/main/docs/guides/browser.md>

## Lokale bewijsgrens

- Vier-frameworkreceipt:
  `evidence/living-core/lab36-eva2-openclaw-hermes-agent-zero-live-2026-08-23.json`
- Geblindeerde C04-receipt:
  `evidence/living-core/lab36-c04-blind-2026-08-24.json`
- LAB-36-catalogus: `../benchmark/eva-agent-benchmark-v1.json`
- Machineleesbare productgate:
  `../benchmark/competitor-capability-matrix-v1.json`
- Fail-closed validator en regressietest:
  `../scripts/lab/check-competitor-capability-matrix.mjs` en
  `../test/competitor-capability-matrix.test.js`
- Technisch brugontwerp: `design/architecture/ecosystem-bridge-v1.md`
