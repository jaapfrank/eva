# Eva Dashboard v1

Eva krijgt een beheerd webdashboard. Het is een **control plane**, geen tweede
agent: alle mutaties gaan via Eva's bestaande policy, append-only eventledger,
versies en waar nodig providerreadback. De interface bevat nooit ruwe secrets.

## V1-schermen

| Scherm | Wat de beheerder kan zien of doen | Veiligheidsgrens |
| --- | --- | --- |
| Overzicht | Actieve A/B-image, health, laatste gesprekken/acties, open blokkades, receipts en release-gates. | Alleen lezen; een groene UI-status is nooit een vervanging voor een receipt. |
| Identiteit | Naam, korte omschrijving, karakter- en antwoordstijl als nieuwe, versieerbare IdentityPack-draft bekijken, wijzigen, diffen en publiceren. | Publicatie maakt een nieuwe pack-hash; oude packs en episodes blijven herleidbaar. Geen vrije systeemprompt-editor. |
| Personen en geheugen | Geverifieerde personen, kanaalbindings, persoonlijke/gedeelde scopes, consent en recall-ledger bekijken; consent intrekken. | Geen privéherinnering van de ene persoon in het scherm van een andere; scope en toestemming worden server-side afgedwongen. |
| Telegram | B-tokenstatus als referentie, geverifieerde DM/groepsroutes, koppelchallenges, deliveryreceipts en teststatus beheren. | Tokens zijn alleen file-secret-referenties; koppelen vereist de wederzijdse private challenge. |
| Skills en capabilities | Gepinde catalogus, uitgever, versie, digest, permissies, reviewstatus en receipts bekijken; alleen een quarantaine-import voorstellen. | Geen automatische installatie of scripts; code blijft in sidecar/quarantaine en Eva houdt policy/ledger/eindantwoord. |
| Instellingen en connectors | Niet-geheime instellingen, configuratie-diffs, connectorstatus en testresultaat bekijken of als draft wijzigen. | Secretwaarden zijn write-only file references; muterende connectoracties volgen prepare → execute → readback. |
| Chat en sessies | Met dezelfde Eva chatten, gesprekken hervatten, model-/token-/kostenstatus en toolreceipts bekijken. | Geen ingebedde shell of tweede chatagent; dezelfde identity-, conversation-, memory- en policyroute als Telegram. |
| Werkbord | Borden, taken, afhankelijkheden, status, opmerkingen, artifacts, receipts, budget en blokkades volgen. | Dunne projectie van Eva's PostgreSQL-ledger; een kaartstatus vervangt nooit extern bewijs. |
| Workers en subagents | Actieve en historische workers, rol, lease, heartbeat, poging, model, token-/kostenverbruik en stopstatus bekijken. | Alleen afgeleide taakgrants; geen ruwe prompt, secrets of ongefilterd persoonsgeheugen in de UI. |
| Eigenaarmodus | Mandaat previewen, gevolgen en grenzen zien, activeren, aanpassen, pauzeren en direct stoppen; actief plan, resterend budget en attempts volgen. | Alleen een geverifieerde eigenaar kan activeren; de UI kan mandaat of providergrants niet stil verruimen. |
| Evaluaties | AgentBench-, GAIA-, OSWorld- en Eva-productsuites per exacte release volgen; manifests, scores, skips, kosten en openbare artifacts openen. | Native en gecontroleerde scoreborden blijven apart; dashboardberekening vervangt nooit de officiële scorer. |

## Kleine opleverslices

1. **D01 — alleen-lezen overzicht:** API + eenvoudige webpagina met B-image,
   gates, receipts en open blokkades; geen mutaties.
2. **D02 — identiteit als versie:** D02a toont nu de geverifieerde actieve
   IdentityPack alleen-lezen; D02b previewt een gebonden staged-presentatiediff
   zonder opslag. Daarna volgen apart een editor, signer/publicatie met hash,
   audit-event en rollback naar een eerder pack.
3. **D03 — personen en Telegram:** pas na de connectorneutrale identiteitslaag;
   bindings, groepsroutes, consent en koppelchallenge.
4. **D04 — skillcatalogus:** gepinde, read-only catalogus en
   quarantine-review; nog geen installatie.
5. **D05 — settings en gecontroleerde acties:** secret-referenties,
   connector-drafts en expliciete approval/readback, alleen wanneer de
   file-secret- en managed-B-grens groen is.
6. **D06 — chat en sessies:** streaming gesprek met dezelfde Eva, een
   gesaneerde sessielijst, hervatten en zichtbare tool-/token-/kostenreceipts;
   geen PTY, shell of tweede modelroute in het dashboard.
7. **D07 — read-only werkbord:** Kanbanprojectie, taakdetails, dependencies,
   workers, pogingen, budgetten, blockers en live gesaneerde updates uit
   dezelfde PostgreSQL-waarheid; zie `delegation-and-workboard-v1.md`.
8. **D08 — gecontroleerde samenwerking:** create, comment, prioriteit, assign,
   pause, cancel, resume en review via dezelfde service als Eva-chat. Geen
   directe browserwrites naar tabellen en geen `done` zonder vereist bewijs.
9. **D09 — Eigenaarmodus:** hetzelfde `OwnerAutonomyMandate/v1` via chat en
   dashboard previewen, activeren, bewaken en stoppen; zie
   `owner-autonomy-mode-v1.md`.
10. **D10 — benchmarkscorebord:** uitsluitend gesaneerde projecties uit
    ongewijzigde, hashgebonden Inspect-logs tonen met links naar scorer en
    heropenbare artifacts; geen tweede eigen scoreformaat; zie
    `comparable-agent-benchmarks-v1.md`.

Elke slice krijgt gerichte tests, een onafhankelijke review en een Git-checkpoint.
De dashboardroute draait uitsluitend in B totdat haar autorisatie- en
privacyproeven groen zijn.

## Toegang vóór bediening

De cockpit kan vanaf checkpoint `D00a` achter één expliciete eigenaarstoegang
staan. De runtime accepteert uitsluitend
`EVA_DASHBOARD_ACCESS_TOKEN_FILE`: een private, reguliere, niet-gelinkte file
van de runtimegebruiker. Een inline omgevingsvariabele wordt geweigerd. Zodra
een dashboard-eigenaarbinding is geconfigureerd, is die file verplicht; de
runtime start dan niet per ongeluk met een zichtbaar werkbord zonder login.

De browser gebruikt gewone HTTP Basic-authenticatie met gebruiker `eva-owner`,
zodat de browser ook de daaropvolgende cockpit-API-calls beschermt zonder het
token in HTML, JavaScript, local storage, cookie of URL te zetten. Beheerde
tools mogen daarnaast dezelfde token via `Authorization: Bearer …` sturen.
De server vergelijkt tokens constant-time, antwoordt bij elke mislukking alleen
met `401`, `no-store` en een algemene challenge, en laat healthchecks buiten
deze gebruikersroute.

Dit sluit alleen de eerste **leesgrens**. De huidige lokale ontwikkelcockpit
blijft expliciet read-only zolang zij zonder toegangstoken is gestart. B krijgt
pas dashboardbediening nadat de ownerbinding, tokenfile-mount, loopback/TLS-
route, CSRF-/originproef en dezelfde chatgebonden service voor iedere write
apart zijn bewezen.
