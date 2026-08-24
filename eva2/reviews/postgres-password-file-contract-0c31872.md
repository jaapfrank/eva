# Onafhankelijke review — PostgreSQL password-file-contract

Commit: `0c31872`
Reviewer: onafhankelijke managed-B-hostcontractreview
Uitkomst: **groen**

## Gecontroleerd

- De PostgreSQL-volume is fail-closed vastgepind op `eva2-postgres` in de
  read-only B-preflight.
- De bootstrapcomponent gebruikt een directe read-only bind met
  `create_host_path: false`, geen Docker `secrets:file`.
- De wachtwoordbestanden, Docker-logs en tijdelijke resourcenamen komen niet in
  de testuitvoer terecht.
- De contracttool maakt alleen UUID-afgeleide testcontainer en -volume aan en
  ruimt alleen die exacte objecten op.
- De documentatie beweert geen volledige managed-B-adoptie of B-deploy.

## Uitgevoerd

```text
PYTHONPATH=eva2:eva2/src python3 -m pytest -q -s \
  eva2/tests/operations/test_core_b_preflight.py \
  eva2/tests/bootstrap/test_postgres_password_file_contract.py
# 24 passed

EVA_POSTGRES_PASSWORD_FILE_HOST=/run/eva2/secrets/postgres-password \
  EVA_STACK_POSTGRES_VOLUME=eva2-postgres \
  docker compose -f eva2/deploy/living-core/postgres-bootstrap.compose.yml config --quiet

python3 eva2/tools/verify_postgres_password_file_contract.py --docker /usr/bin/docker
# fresh_volume: passed; initialized_volume: passed
```

De live proef gebruikte uitsluitend een tijdelijke PostgreSQL-container en een
uniek testvolume. B is niet geïnspecteerd, gekoppeld, herstart of gewijzigd.
