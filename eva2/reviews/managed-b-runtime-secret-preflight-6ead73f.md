# Onafhankelijke review — managed B runtime-secretpreflight

Commit: `6ead73f`
Reviewer: onafhankelijke managed-B-hostcontractreview
Uitkomst: **groen**

## Gecontroleerd

- Home Assistant-token, database-DSN, Telegram-token en Telegram-bindings gaan
  elk door dezelfde metadata-only preflight.
- De controle eist een absoluut regulier niet-symlinkbestand, exact runtime-UID,
  owner-read, geen group/other-permissies, 1..64 KiB en stabiele `O_PATH` /
  `O_NOFOLLOW` metadata.
- De preflight leest nooit secretbytes.
- De twee Telegram-hostpaden zijn verplichte B-configuratie en onveilige modes
  worden afzonderlijk geweigerd.

## Uitgevoerd

```text
PYTHONPATH=eva2:eva2/src python3 -m pytest -q -s \
  eva2/tests/operations/test_core_b_preflight.py
# 24 passed

python3 -m py_compile eva2/src/eva/operations/core_b_preflight.py
python3 -m ruff check eva2/src/eva/operations/core_b_preflight.py \
  eva2/tests/operations/test_core_b_preflight.py
```

Dit is een lokale preflightcontracttest. B is niet geïnspecteerd, herstart of
gewijzigd; PostgreSQLs root-owned passwordfile en het Hindsight-volume blijven
aparte adoptiecontroles.
