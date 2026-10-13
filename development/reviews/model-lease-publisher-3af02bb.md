# Onafhankelijke review — modellease-publisher `3af02bb`

Datum: 24 augustus 2026.

## Bevestigd

- De private credentialbron gaat door de geharde fileloader.
- Leasemappen moeten vooraf bestaan en exact voldoen aan hun owner/group/mode;
  de publisher maakt geen verborgen fallbackdirectory aan.
- Tijdelijke bestanden gebruiken directory-fd, `O_EXCL` en `O_NOFOLLOW`; na
  write volgt file- en directory-`fsync` rond de atomaire replace.
- Eva's gedeelde lease is `1000:65532`/`0640`; de private Codex-variant is
  `1000:1000`/`0600`.
- 21 gerichte tests, compile en Ruff zijn groen.

## Grens

De test draait met lokale uid/gid-contracten omdat de host geen testgroep
`65532` heeft. Hostprovisioning en B-deployment zijn dus nog niet bewezen.
