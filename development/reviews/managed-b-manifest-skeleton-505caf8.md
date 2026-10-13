# Onafhankelijke review — managed B manifest-skelet

Commit: `505caf8`
Reviewer: onafhankelijke managed-B-hostcontractreview
Uitkomst: **groen**

## Gecontroleerd

- Eén Telegram-first grafiek met exact acht services:
  PostgreSQL, schema-installer, twee leasepublishers, Hindsight, core,
  duurzame-takenworker en Telegram-ingress.
- De schema-installer is uitsluitend een eenmalig profiel, geen dependency die
  de normale steady-state start blokkeert.
- Core behoudt principal-, fan-out- en duurzame-doelconfiguratie.
- Eén cloud/multilingual Hindsight-route, geen Docker `secrets:file`, alleen
  lange private binds met `create_host_path: false` en alleen de twee bedoelde
  lease-outputdirectories als schrijfbaar.
- Ieder service heeft dezelfde managed/configuration-hash labels; de drie
  named volumes zijn verzegelde invoer.

## Uitgevoerd

```text
PYTHONPATH=eva2:eva2/src python3 -m pytest -q -s \
  eva2/tests/bootstrap/test_managed_b_manifest.py
# 3 passed

docker compose -f eva2/deploy/living-core/managed-b.compose.yml config --quiet
# groen met alleen inerte, niet-geheime interpolatiewaarden
```

Dit is een manifestcontract, geen B-adoptie. De preflight moet nog alle
hostbestanden, het Hindsight-volume en de uiteindelijke gerenderde grafiek aan
één sealsnapshot koppelen; B is niet geïnspecteerd of gewijzigd.
