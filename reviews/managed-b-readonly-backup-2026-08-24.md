# Onafhankelijke live review — B-back-up vóór adoptie

Uitgevoerd: 24 augustus 2026
Reviewer: onafhankelijke managed-B-hostcontractreview
Uitkomst: **groen**

## Bron en herstelset

Alle bronmounts waren read-only. De archieven staan buiten de repository in
`/home/administrator/eva2-adoption-backup.KIVXGH`, waarvan de directory mode
`0700` en ieder archief mode `0600` heeft.

| Bronvolume | Archief | SHA-256 |
|---|---|---|
| `eva2-postgres-b-live` | `postgres.tar` | `3c4ca9420d1bff73beee81a73acba61f5cb6f16972b299cbf30bef33f4c34ddd` |
| `eva2-living-work-b` | `workspace.tar` | `49d5242cf7d95e15665c58c723534f48fd9c318c16e7f35d7015e4f9cf2b212c` |
| `eva2-hindsight-multilingual-trusted-v2-20260824` | `hindsight.tar` | `e064c4b0823f6e9da3c3e9eaa0ec3481f435bf8017f422a60605d3e317ad75cc` |

## Gecontroleerd

- De drie bronvolumes bestaan volgens Docker-metadata.
- Alle drie SHA-256 waarden kwamen overeen met de aangemaakte archieven.
- `tar -tf` controleerde elk archief zonder inhoud of bestandsnamen te tonen.
- Geen tijdelijke `eva2-adoption-backup-*` container bleef achter.
- `eva2-living-core-b` bleef healthy en `eva2-postgres-b-live` bleef draaien.

De review las geen archief- of volume-inhoud, geen secrets en wijzigde B niet.
Dit is herstelbewijs vóór adoptie, geen B-adoptie of eindreleasebewijs.
