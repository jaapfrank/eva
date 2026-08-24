# Onafhankelijke review — modellease-consumer `5303622`

Datum: 24 augustus 2026.

## Bevestigd

- De runtime leest een modellease alleen via een vaste `1000:65532`/`0640`
  gedeelde-filecontract.
- De reader controleert type, symlink, hardlinks, eigenaar, groep, exacte mode,
  bestandsgrootte en fd-identiteit vóór én na de read.
- Modeltests op de Windows-mount mocken uitsluitend de reader; de werkelijke
  permissiontest draait onder native `/tmp`.
- 45 gerichte tests, compile en Ruff zijn groen.

## Grens

De publisher en geprovisioneerde hostdirectory horen bij de volgende slice.
Er is geen B-deployment uitgevoerd.
