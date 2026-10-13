# Onafhankelijke review — managed-B hostcontract `ce23545`

Datum: 24 augustus 2026.

## Bevestigd

- De B-preflight eist de exacte workspacevolume-naam `eva2-living-work`.
- Codex-authority, Eva-modellease en Hindsight-lease moeten elk bestaan als
  reguliere niet-symlink directory met hun exacte owner/group/modecontract.
- Preflight vergelijkt ook de geopende directory-fd met de vooraf gelezen
  metadata en weigert `/`, een verkeerde mode of een alternatief volume.
- 20 gerichte preflighttests en compile zijn groen.

## Grens

Dit controleert alleen plan- en hostmetadata. Er is geen huidige B geïnspecteerd
of veranderd, en PostgreSQL-bootstrap plus de zelfstandige adoptatiemanifestset
blijven aparte gates.
