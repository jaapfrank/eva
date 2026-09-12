# Renovate npm coverage — 12 september 2026

Scope: configuratievoorstel op basiscommit `7affebbdeec5e9f53310eaecb2dbacfdc6fc9ca1`. Geen botactivatie, runtimewijziging, dependency-update of eigenaaracceptatie.

## Vastgesteld gat en wijziging

`renovate.json` beperkte enabledManagers tot custom.regex, docker-compose, dockerfile, github-actions, pep621 en pip_requirements. npm ontbrak. De bestaande lokale dry-run van 30 augustus bevatte daarom geen npm-extractie. Die dry-run vermeldt expliciet dat GitHub-automatisering niet actief was; deze wijziging bewijst geen latere activatie.

Toegevoegd: npm aan enabledManagers en aan de bestaande voorstelregel met automerge=false en needs-eva-validation. ignoreScripts=true staat nu expliciet vast; dit was al de Renovate-standaard. Geen postUpgradeTasks, eigen scanner of extra beheerlaag toegevoegd. Dashboardgoedkeuring, minimumleeftijd, rate limits, pinning, ignorePaths en alle andere managers/regels blijven gelijk. De configuratie activeert zelf geen runner. Een toekomstige runner moet zijn bestaande read-only/dry-run- en uitvoeringsgrenzen behouden; deze repositoryconfig kent geen nieuwe platformrechten toe.

## Offline bewijs

Bestaande lokale Renovate 44.52.0 gebruikt; niets geïnstalleerd of opgewaardeerd. De officiële config-validator geeft exit 0: config validated successfully. Gerichte fixture gebruikt Renovates echte npm extractPackageFile met skipInstalls=true, allowScripts=false en allowedCommands=[]. Geen lookup-, update-, lockfilegeneratie- of platformfunctie aangeroepen. Vier bestaande manifests en hun bijbehorende lockfiles gevonden:

| Manifest | Geëxtraheerde dependencies incl. engines |
| --- | ---: |
| package.json | 17 |
| eva2/deploy/chrome-bridge/package.json | 1 |
| eva2/experiments/filesystem-mcp-mutation-v1/package.json | 1 |
| eva2/experiments/react-admin-dashboard-v1/package.json | 6 |

Fixture: `eva2/reviews/renovate-npm-coverage-check-2026-09-12.mjs <checkout> <installed-renovate-directory>`; exit 0. Een eerste fixtureversie las npmLock op het verkeerde objectniveau en faalde; na gebruik van Renovates managerData.npmLock slagen de lockfileasserties. Dit was een fixturefout, geen extractiefout. De library meldt een niet-geïnitialiseerde logger; geen extractiewaarschuwing of lookupresultaat wordt daarmee geclaimd.

De gedeelde index heeft gelijktijdige staged deletions/untracked chrome-bridge; inventarisatie is daarom expliciet op HEAD van de geïsoleerde checkout gedaan. Die heeft vier projecten. Geen gedeelde index gewijzigd.

## Grenzen en vervolg

Dit bewijst configuratievaliditeit en bestaande npm-extractie, niet dat een actieve bot PRs maakt, updates veilig installeert of afhankelijkheden kwetsbaarheidsvrij zijn. Geen lockfiles aangepast. Onafhankelijke review vóór integratie. Daarna kan de bestaande gecontroleerde updateketen dit meenemen zonder runtimecutover.

Gelezen: renovate.json, component-watch.json, OPEN_SOURCE_COMPONENTEN.md, AGENTS.md, eva2/PROJECTDOEL.md, release SBOM/signature/revocation/buildbeleid en de historische lokale Renovate-dry-run. Bestaande security/releasepoorten blijven gelden voor daadwerkelijke updates.

Primaire Renovate-contracten: [npm manager](https://docs.renovatebot.com/modules/manager/npm/), [ignoreScripts](https://docs.renovatebot.com/configuration-options/#ignorescripts), [self-hosted allowScripts](https://docs.renovatebot.com/self-hosted-configuration/#allowscripts). De fixture gebruikt de al beschikbare 44.52.0; er is geen claim dat dit de nieuwste release is.