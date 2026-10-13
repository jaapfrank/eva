# Onafhankelijke review — schema-installer `9e129a4`

Datum: 24 augustus 2026.

## Bevestigd

- De slice raakt uitsluitend de schema-installer, haar deploymentprofiel,
  secretgrensinventaris en gerichte tests.
- Een inline `EVA_DATABASE_DSN` faalt; alleen `EVA_DATABASE_DSN_FILE` gaat
  door de bestaande harde private-fileloader.
- Het aparte profiel draait als `65532:65532`, read-only, zonder capabilities,
  met `no-new-privileges`, een beperkte `noexec`-tmpfs en een read-only bind
  zonder automatische hostpadcreatie.
- `PYTHONPATH=eva2/src:eva2 pytest --capture=no -q
  eva2/tests/operations/test_install_schema.py
  eva2/tests/bootstrap/test_schema_install_deployment.py
  eva2/tests/bootstrap/test_secret_files.py`: 14 groen.
- `PYTHONPATH=eva2/src:eva2 python3 -m compileall -q eva2/src/eva`: groen.

## Grens

Dit is geen B-deployment of live databasebewijs. De legacy
`stack.compose.yml`-migratieroute blijft bewust buiten het beheerde B-pad.
