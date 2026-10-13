# 4D 64-schema kandidaat — beperkte receipt

Datum: 24 augustus 2026.

## Afbakening

- Kandidaat-worktree: `/tmp/eva2-4d-64-schema-tYHK0W`.
- Branch: `recovery/v2-4d-64-schema-candidate`.
- Kandidaatcommit: `5fafab8`, met ouder `5f72d59`.
- De kandidaat bevat de historische registry met 64 schema's (`fcf665…`) en
  de herstelde 4D-authoritybron met schedule `f492…`.
- Hoofdbranch, B-containers, B-volumes en secrets zijn niet gebruikt of
  gewijzigd.

## Uitgevoerd bewijs

1. Statische kandidaatcheck:

   ```sh
   PYTHONDONTWRITEBYTECODE=1 python3 eva2/tools/check_4d_freeze.py --root .
   ```

   Resultaat: `"status":"passed"`, zonder fouten.

2. Positieve en fail-closed freeze-tests:

   ```sh
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=eva2/src:. pytest -q -s \
     eva2/tests/design/test_4d_freeze.py::FourDPreCodeFreezeTests::test_repository_freeze_passes_and_reports_all_ten_freeze_hashes
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=eva2/src:. pytest -q -s \
     eva2/tests/design/test_4d_freeze.py::FourDPreCodeFreezeTests::test_duplicate_profile_key_fails_before_semantic_checks
   ```

   Resultaat: beide groen.

3. Disposable PostgreSQL-geheugen/herstarttest:

   - Nieuwe PostgreSQL 16-container met een uitsluitend tijdelijk `tmpfs`
     datavolume, database `eva2_4b_disposable_4d` en uitsluitend localhost.
   - De projectdriver `psycopg` 3 is expliciet gekozen in de DSN
     (`postgresql+psycopg://…`); de kandidaat pinnt
     `psycopg[binary]==3.3.4`.
   - Uitgevoerd:

     ```sh
     EVA2_TEST_POSTGRES_DSN='postgresql+psycopg://[redacted]@127.0.0.1:55432/eva2_4b_disposable_4d' \
       PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=eva2/src:. pytest -q -s \
       eva2/tests/postgres/test_memory_repository.py::test_real_postgres_remember_correct_recall_and_restart
     ```

   - Resultaat: `1 passed`. De test migreert, schrijft, herstart/corrigeert
     en haalt feiten terug via `PostgresMemoryRepository`; daarna draait zij
     de disposable database terug naar de basis.
   - De nieuwe container is direct na de test gestopt en door `--rm`
     verwijderd.

## Onafhankelijke controle

Een onafhankelijke reviewer bevestigde de bronafbakening en de begrensde
componentclaim van deze receipt. Dit is geen volledige 4D-gate, geen
B-adoptie en geen product-liveclaim.
