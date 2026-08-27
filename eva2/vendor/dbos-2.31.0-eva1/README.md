# DBOS 2.31.0+eva.1 kandidaat

Dit is een geïsoleerde rebase-kandidaat. Hij behoudt Eva's twee vereiste
grenzen: runtime opent een al gemigreerde database alleen met een read-only
schema-check, en een tijdelijke transactie- of pre-checkfout heeft hoogstens
drie pogingen. Een onduidelijke PostgreSQL-commit wordt niet blind herhaald.

De kandidaat wordt pas de runtimeversie nadat de vaste wheel, de volledige
PostgreSQL-crashreeks, onafhankelijke review en de release-inputs allemaal
groen zijn. Tot dan blijft `2.30.0+eva.1` de enige toegelaten runtimeversie.
