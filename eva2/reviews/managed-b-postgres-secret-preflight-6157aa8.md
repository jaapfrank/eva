# Onafhankelijke review — PostgreSQL secret-preflight

Commit: `6157aa8`
Reviewer: onafhankelijke managed-B-hostcontractreview
Uitkomst: **groen**

## Gecontroleerd

- `EVA_POSTGRES_PASSWORD_FILE_HOST` is verplichte B-configuratie.
- De productiecode eist eigenaar UID `0`, exact mode `0600`, regulier
  niet-symlinkbestand en een stabiele metadata-only `O_PATH`/`O_NOFOLLOW`
  controle; secretbytes worden niet gelezen.
- Een expliciete test roept de ongemodificeerde productiedefault aan en bewijst
  dat een niet-root bestand wordt geweigerd.
- De testinvoeging voor tijdelijke bestanden bestaat alleen in de testwrapper
  en verandert het productiedefault niet.

## Uitgevoerd

```text
PYTHONPATH=eva2:eva2/src python3 -m pytest -q -s \
  eva2/tests/operations/test_core_b_preflight.py
# 29 passed
```

Dit valideert alleen de toekomstige hostcontractroute. De bestaande B en haar
secrets zijn niet benaderd.
