# RR-002 tot en met RR-004 — onafhankelijke herstelreview

Besluit: **GO** voor deze drie herstelstappen, en niet voor deployment of de
volledige Eva2-release.

De beoordeelde kandidaat hoort bij broncommit
`c1d4c8a679a9651c7969a1b11591d3d1c770088e` en releaseauthority
`41e5393990759a87e3d44c6e7fe0507380e173d326480ee675d54a1278def448`.

## RR-002 — twee onafhankelijke builds

De twee lege offline bouwers hadden verschillende daemon-, socket-, state-,
namespace- en BuildKit-identiteiten. Hun toelatingsreceipts hebben respectievelijk
SHA-256 `2aab973c75cd9dcaabb9653a37edf11b7647e6973cfe0f48dc4da347829f1d1e`
en `2e3bfd624262ab4e4668857da9df4f20ed6bbeb8e455222e794bfcd835e3614a`.
Beide cleanupreceipts melden dat processen, units, namespaces en tijdelijke
runtimepaden verdwenen zijn.

## RR-003 — vergelijking

De onafhankelijke vergelijking kreeg **GO**. Het private resultaat heeft
SHA-256 `b9afd26aae434b820c1643e7ddb9bee4b80965f58afe6ef6db9c6cd79067dc42`.
Beide builds hebben hetzelfde kandidaatarchief, OCI-manifest, dezelfde
configuratie, laagvolgorde en runtimebron. Het kandidaatarchief heeft SHA-256
`d2d0c04436e8422c0fc7dddcc11a66b8274afe9dd5d04c7c6562f089b5972b42`.

## RR-004 — SBOM en scan

De kandidaat is rechtstreeks uit het bewaarde archief gelezen; zij is niet
gestart. Syft `1.51.0` legde 75 pakketten en 1.519 bestanden vast. Trivy
`0.74.0` gebruikte de vooraf vastgezette lokale database, zonder netwerk of
pull, en vond 0 hoge en 0 kritieke kwetsbaarheden. Het private scanreceipt heeft
SHA-256 `e10720dd1f35a76e7c85534f56bf0e7c5b2abc912c743ac92319fb46a32a4da2`.

De scanreview controleerde de bewaarde JSON, hashes, kandidaatbindingen,
scannerpins en cacheauthority. Zij heeft de Docker-opdrachten niet opnieuw
uitgevoerd; de offline-eigenschappen zijn dus gebonden aan het bewaarde receipt
en de daadwerkelijk gebruikte commandoregel.

## Grens

Dit bewijst een reproduceerbare en op HIGH/CRITICAL schone kandidaat. Het
bewijst geen volledige regressie, echte gebruikerskwaliteit, voorsprong op
concurrenten of veilige livepromotie. Die onderwerpen blijven afzonderlijke
product- en releasebeslissingen.
